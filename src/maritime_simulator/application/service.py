# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
import time
from typing import Protocol

from maritime_simulator.domain.commands import (
    Command,
    ExternalActuatorOrder,
    SetExternalActuatorPolicy,
    SeekRoute,
    SetPosition,
    SetRoute,
    SetRouteOptions,
    UpdateRoute,
)
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.profiles import VesselProfileStore
from maritime_simulator.domain.state import SimulationSnapshot, SimulationState
from maritime_simulator.simulation.clock import ClockAdvance, SimulationClock
from maritime_simulator.simulation.dynamics import VesselDynamics
from maritime_simulator.simulation.environment import calculate_wind

from .arbitration import CommandArbitrator
from .metrics import PerformanceMonitor


@dataclass(frozen=True, slots=True)
class TrackControlOutput:
    rudder_order_starboard_deg: float
    rudder_order_port_deg: float | None = None
    progress_nm: float | None = None
    xte_nm: float | None = None
    next_waypoint_index: int | None = None
    phase: str | None = None
    route_completed: bool | None = None


@dataclass(slots=True)
class _LatestActuatorOrder:
    command: ExternalActuatorOrder


class TrackController(Protocol):
    def update(
        self,
        state: SimulationState,
        profile_display_name: str,
        dt_s: float,
    ) -> TrackControlOutput: ...

    def reset(self) -> None: ...


class SimulatorService:
    """Single mutation boundary for V2 simulation state."""

    def __init__(
        self,
        *,
        profile_file: Path | None = None,
        clock: SimulationClock | None = None,
        dynamics: VesselDynamics | None = None,
        arbitrator: CommandArbitrator | None = None,
        track_controller: TrackController | None = None,
        performance_monitor: PerformanceMonitor | None = None,
        initial_state: SimulationState | None = None,
        monotonic=time.monotonic,
    ):
        self.profiles = VesselProfileStore(profile_file)
        self.clock = clock or SimulationClock()
        self.dynamics = dynamics or VesselDynamics()
        self.arbitrator = arbitrator or CommandArbitrator()
        self.track_controller = track_controller
        self.performance_monitor = performance_monitor or PerformanceMonitor()
        self._state = deepcopy(initial_state) if initial_state is not None else SimulationState()
        self._commands: deque[Command | _LatestActuatorOrder] = deque()
        self._pending_actuator_orders: dict[tuple[str, str], _LatestActuatorOrder] = {}
        self._lock = RLock()
        self._monotonic = monotonic
        self._closed = False

    def submit(self, command: Command) -> None:
        with self._lock:
            self._ensure_open()
            if isinstance(command, ExternalActuatorOrder):
                if command.actuator not in {"bow_thruster", "engine"} or command.protocol not in {"TRC", "PRC", "XDR"}:
                    return
                key = (command.actuator, command.protocol)
                pending = self._pending_actuator_orders.get(key)
                if pending is not None:
                    pending.command = command
                else:
                    pending = _LatestActuatorOrder(command)
                    self._pending_actuator_orders[key] = pending
                    self._commands.append(pending)
                return
            if isinstance(command, SetExternalActuatorPolicy):
                # Do not coalesce samples across a policy/configuration boundary.
                for key in tuple(self._pending_actuator_orders):
                    if key[0] == command.actuator:
                        del self._pending_actuator_orders[key]
            self._commands.append(command)

    def snapshot(self) -> SimulationSnapshot:
        with self._lock:
            return self._state.snapshot()

    def tick(self, wall_time_s: float | None = None) -> ClockAdvance:
        with self._lock:
            self._ensure_open()
            advance = self.clock.tick(wall_time_s)
            self._apply_advance(advance)
            return advance

    def advance_wall_time(self, wall_elapsed_s: float) -> ClockAdvance:
        with self._lock:
            self._ensure_open()
            advance = self.clock.advance_wall_time(wall_elapsed_s)
            self._apply_advance(advance)
            return advance

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._commands.clear()
            self._pending_actuator_orders.clear()
            controller_close = getattr(self.track_controller, "close", None)
            if callable(controller_close):
                controller_close()

    def _ensure_track_controller(self) -> TrackController:
        if self.track_controller is None:
            from maritime_simulator.simulation.track_adapter import LegacyTrackControllerAdapter

            self.track_controller = LegacyTrackControllerAdapter()
        return self.track_controller

    def _apply_advance(self, advance: ClockAdvance) -> None:
        monitor = self.performance_monitor
        with monitor.measure("simulation_tick"):
            self._drain_commands()
            profile = self.profiles.get(self._state.vessel_profile_id)
            now = self._monotonic()
            if not advance.fixed_steps_s:
                self.arbitrator.resolve(self._state, profile, now)
                calculate_wind(self._state)
                self._state.revision += 1
                return

            for dt_s in advance.fixed_steps_s:
                if self._state.control_mode is ControlMode.TRACK:
                    if self._state.route.completed and self._state.route.end_action == "Stop":
                        self.arbitrator.set_track_rudder_order(0.0, 0.0, profile)
                        self._state.route.phase = "completed"
                    else:
                        with monitor.measure("track_controller"):
                            controller = self._ensure_track_controller()
                            profile_setter = getattr(controller, "set_vessel_profile", None)
                            if callable(profile_setter):
                                profile_setter(profile)
                            output = controller.update(self._state, profile.display_name, dt_s)
                        self.arbitrator.set_track_rudder_order(
                            output.rudder_order_starboard_deg,
                            output.rudder_order_port_deg,
                            profile,
                        )
                        if output.progress_nm is not None:
                            self._state.route.progress_nm = output.progress_nm
                        if output.xte_nm is not None:
                            self._state.route.xte_nm = output.xte_nm
                        if output.next_waypoint_index is not None:
                            self._state.route.next_waypoint_index = output.next_waypoint_index
                        if output.phase is not None:
                            self._state.route.phase = output.phase
                        if output.route_completed is not None:
                            was_completed = self._state.route.completed
                            self._state.route.completed = output.route_completed
                            if output.route_completed and not was_completed:
                                self._apply_route_end_action()
                self.arbitrator.resolve(self._state, profile, now)
                if self._state.control_mode is not ControlMode.SENSOR:
                    with monitor.measure("vessel_dynamics"):
                        self.dynamics.integrate(self._state, profile, dt_s)
                else:
                    self._integrate_sensor_control(dt_s)
                self._state.simulation_time_s += dt_s
                self._state.revision += 1
                monitor.increment("simulation_steps")
            monitor.set_gauge("simulation_time_s", self._state.simulation_time_s)
            monitor.set_gauge("time_scale", self._state.time_scale)

    def _drain_commands(self) -> None:
        profile_changed = False
        route_state_changed = False
        while self._commands:
            command = self._commands.popleft()
            if isinstance(command, _LatestActuatorOrder):
                key = (command.command.actuator, command.command.protocol)
                if self._pending_actuator_orders.get(key) is command:
                    del self._pending_actuator_orders[key]
                command = command.command
            previous_profile = self._state.vessel_profile_id
            requested_scale = self.arbitrator.apply_command(self._state, command, self.profiles)
            if requested_scale is not None:
                self.clock.set_time_scale(requested_scale)
                self._state.time_scale = self.clock.time_scale
            profile_changed = profile_changed or previous_profile != self._state.vessel_profile_id
            route_state_changed = route_state_changed or isinstance(
                command,
                (SetRoute, UpdateRoute, SetRouteOptions, SeekRoute, SetPosition),
            )
        if (profile_changed or route_state_changed) and self.track_controller is not None:
            self.track_controller.reset()

    def route_visuals(self) -> tuple[list[dict], list[dict]]:
        """Return controller-aligned WOP markers and turn arcs for the active route."""
        with self._lock:
            if len(self._state.route.waypoints) < 3:
                return [], []
            controller = self._ensure_track_controller()
            visualizer = getattr(controller, "route_visuals", None)
            if not callable(visualizer):
                return [], []
            profile = self.profiles.get(self._state.vessel_profile_id)
            profile_setter = getattr(controller, "set_vessel_profile", None)
            if callable(profile_setter):
                profile_setter(profile)
            return visualizer(self._state, profile.display_name)

    def _integrate_sensor_control(self, dt_s: float) -> None:
        sensor = self._state.sensor_control
        motion = self._state.motion
        if sensor.heading_follows_rot and abs(motion.rate_of_turn_deg_min) > 1e-12:
            motion.heading_deg = (motion.heading_deg + motion.rate_of_turn_deg_min * dt_s / 60.0) % 360.0
            if sensor.cog_follows_heading:
                motion.cog_deg = (motion.heading_deg + sensor.cog_heading_offset_deg) % 360.0
        calculate_wind(self._state)

    def _apply_route_end_action(self) -> None:
        route = self._state.route
        if route.end_action == "Stop" or len(route.waypoints) < 2:
            return
        if route.end_action == "Reverse":
            route.waypoints = tuple(reversed(route.waypoints))
        first, second = route.waypoints[0], route.waypoints[1]
        route.progress_nm = 0.0
        route.xte_nm = 0.0
        route.next_waypoint_index = 1
        route.phase = "route_restart"
        route.completed = False
        self._state.position.latitude_deg = first.latitude_deg
        self._state.position.longitude_deg = first.longitude_deg
        from maritime_simulator.simulation.route_geometry import bearing_deg

        heading = bearing_deg(first, second)
        self._state.motion.heading_deg = heading
        self._state.motion.cog_deg = heading
        if self.track_controller is not None:
            self.track_controller.reset()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("SimulatorService is closed")
