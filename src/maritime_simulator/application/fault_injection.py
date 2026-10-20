# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import replace
import math
from threading import RLock

from maritime_simulator.domain.state import SimulationSnapshot
from maritime_simulator.protocols.sensors import SensorSnapshot


FAULT_SCENARIOS = (
    ("gnss_jamming", "GNSS jamming / no fix", "GNSS position sentences report an invalid fix."),
    ("gnss_intermittent", "Intermittent GNSS", "GNSS reception alternates between valid and invalid every five seconds."),
    ("gnss_drift", "GNSS position drift", "The reported position drifts gradually away from truth."),
    ("gnss_spoofing", "GNSS spoofing", "The reported position follows a plausible displaced track."),
    ("heading_bias", "Heading bias", "Heading is reported ten degrees high."),
    ("heading_freeze", "Heading freeze", "Heading remains at the value present when the fault is activated."),
    ("speed_scale", "Speed log scale error", "SOG and STW are reported twenty percent low."),
    ("speed_freeze", "Speed freeze", "SOG and STW remain at their activation values."),
    ("rot_bias", "Rate-of-turn bias", "ROT is reported two degrees per minute high."),
    ("rudder_freeze", "Rudder feedback freeze", "Rudder response remains at its activation value."),
    ("depth_bias", "Depth offset", "Depth is reported five metres shallower than truth."),
    ("wind_freeze", "Wind sensor freeze", "True and relative wind remain at their activation values."),
    ("gnss_output_loss", "GNSS sentence loss", "All regular GNSS output sentences are suppressed."),
    ("packet_loss", "Intermittent packet loss", "Every fifth scheduled output sentence is suppressed."),
    ("checksum_error", "Checksum corruption", "Every tenth output sentence receives an invalid checksum."),
    ("output_delay", "Output latency", "Scheduled output is delayed by 500 milliseconds."),
)

GNSS_PROTOCOLS = frozenset({"DTM", "GGA", "GLL", "GNS", "RMC", "VTG", "ZDA"})


class FaultInjectionController:
    """Thread-safe post-model sensor and transmission fault injection."""

    def __init__(self):
        self._active: set[str] = set()
        self._configured: set[str] = set()
        self._frozen: dict[str, tuple[float, ...]] = {}
        self._revision = 0
        self._transmission_count = 0
        self._lock = RLock()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def active_faults(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active))

    def configured_faults(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._configured))

    def set_active(self, fault_id: str, active: bool) -> None:
        fault_id = self._validate(fault_id)
        with self._lock:
            if active:
                self._active.add(fault_id)
                self._configured.add(fault_id)
            else:
                self._active.discard(fault_id)
                self._configured.discard(fault_id)
                self._frozen.pop(fault_id, None)
            self._revision += 1

    def activate_configured(self) -> None:
        with self._lock:
            self._active = set(self._configured)
            self._frozen.clear()
            self._transmission_count = 0
            self._revision += 1

    def clear(self) -> None:
        with self._lock:
            if self._active or self._frozen:
                self._active.clear()
                self._frozen.clear()
                self._transmission_count = 0
                self._revision += 1

    def export_state(self) -> dict:
        with self._lock:
            configured = sorted(self._configured | self._active)
        return {"configured_faults": configured, "activate_on_startup": False}

    def apply_state(self, state: dict | None) -> None:
        configured = set()
        if isinstance(state, dict):
            for fault_id in state.get("configured_faults", ()):
                try:
                    configured.add(self._validate(str(fault_id)))
                except ValueError:
                    continue
        with self._lock:
            self._configured = configured
            self._active.clear()
            self._frozen.clear()
            self._transmission_count = 0
            self._revision += 1

    def apply_sensor(
        self,
        sensor: SensorSnapshot,
        simulation: SimulationSnapshot,
    ) -> SensorSnapshot:
        with self._lock:
            active = set(self._active)
            frozen = self._frozen
            if not active:
                return sensor
            values = {}
            if "gnss_jamming" in active:
                values["gnss_valid"] = False
            if "gnss_intermittent" in active and int(simulation.simulation_time_s // 5.0) % 2:
                values["gnss_valid"] = False
            if "gnss_drift" in active:
                drift_m = min(5_000.0, simulation.simulation_time_s * 25.0 / 60.0)
                values["latitude_deg"] = sensor.latitude_deg + drift_m / 111_120.0
                values["longitude_deg"] = sensor.longitude_deg + drift_m / (
                    111_120.0 * max(0.1, math.cos(math.radians(sensor.latitude_deg)))
                )
            if "gnss_spoofing" in active:
                phase = math.radians((simulation.simulation_time_s / 30.0) % 360.0)
                north_m = 926.0 + 90.0 * math.sin(phase)
                east_m = 250.0 * math.cos(phase)
                values["latitude_deg"] = sensor.latitude_deg + north_m / 111_120.0
                values["longitude_deg"] = sensor.longitude_deg + east_m / (
                    111_120.0 * max(0.1, math.cos(math.radians(sensor.latitude_deg)))
                )
            if "heading_bias" in active:
                values["heading_deg"] = (sensor.heading_deg + 10.0) % 360.0
            if "heading_freeze" in active:
                frozen.setdefault("heading_freeze", (sensor.heading_deg,))
                values["heading_deg"] = frozen["heading_freeze"][0]
            if "speed_scale" in active:
                values.update(sog_kn=sensor.sog_kn * 0.8, stw_kn=sensor.stw_kn * 0.8)
            if "speed_freeze" in active:
                frozen.setdefault("speed_freeze", (sensor.sog_kn, sensor.stw_kn))
                values.update(sog_kn=frozen["speed_freeze"][0], stw_kn=frozen["speed_freeze"][1])
            if "rot_bias" in active:
                values["rot_deg_min"] = sensor.rot_deg_min + 2.0
            if "rudder_freeze" in active:
                frozen.setdefault(
                    "rudder_freeze",
                    (sensor.rudder_response_starboard_deg, sensor.rudder_response_port_deg),
                )
                values.update(
                    rudder_response_starboard_deg=frozen["rudder_freeze"][0],
                    rudder_response_port_deg=frozen["rudder_freeze"][1],
                )
            if "depth_bias" in active:
                values["depth_m"] = max(0.0, sensor.depth_m - 5.0)
            if "wind_freeze" in active:
                frozen.setdefault(
                    "wind_freeze",
                    (
                        sensor.true_wind_from_deg,
                        sensor.true_wind_speed_kn,
                        sensor.relative_wind_from_bow_deg,
                        sensor.relative_wind_speed_kn,
                    ),
                )
                values.update(
                    true_wind_from_deg=frozen["wind_freeze"][0],
                    true_wind_speed_kn=frozen["wind_freeze"][1],
                    relative_wind_from_bow_deg=frozen["wind_freeze"][2],
                    relative_wind_speed_kn=frozen["wind_freeze"][3],
                )
            return replace(sensor, **values)

    def display_snapshot(
        self,
        simulation: SimulationSnapshot,
        sensor: SensorSnapshot,
    ) -> SimulationSnapshot:
        return replace(
            simulation,
            latitude_deg=sensor.latitude_deg,
            longitude_deg=sensor.longitude_deg,
            heading_deg=sensor.heading_deg,
            cog_deg=sensor.cog_deg,
            sog_kn=sensor.sog_kn,
            stw_kn=sensor.stw_kn,
            rate_of_turn_deg_min=sensor.rot_deg_min,
            rudder_response_starboard_deg=sensor.rudder_response_starboard_deg,
            rudder_response_port_deg=sensor.rudder_response_port_deg,
            depth_m=sensor.depth_m,
            true_wind_from_deg=sensor.true_wind_from_deg,
            true_wind_speed_kn=sensor.true_wind_speed_kn,
            relative_wind_from_bow_deg=sensor.relative_wind_from_bow_deg,
            relative_wind_speed_kn=sensor.relative_wind_speed_kn,
        )

    def transmission_delay_s(self) -> float:
        with self._lock:
            return 0.5 if "output_delay" in self._active else 0.0

    def transform_sentence(self, protocol: str, sentence: str) -> str | None:
        with self._lock:
            self._transmission_count += 1
            count = self._transmission_count
            active = set(self._active)
        if "gnss_output_loss" in active and str(protocol).upper() in GNSS_PROTOCOLS:
            return None
        if "packet_loss" in active and count % 5 == 0:
            return None
        if "checksum_error" in active and count % 10 == 0:
            return _corrupt_checksum(sentence)
        return sentence

    @staticmethod
    def _validate(fault_id: str) -> str:
        known = {item[0] for item in FAULT_SCENARIOS}
        if fault_id not in known:
            raise ValueError(f"Unknown fault scenario: {fault_id}")
        return fault_id


def _corrupt_checksum(sentence: str) -> str:
    marker = sentence.rfind("*")
    if marker < 0 or marker + 2 >= len(sentence):
        return sentence
    replacement = "0" if sentence[marker + 1].upper() != "0" else "F"
    return sentence[: marker + 1] + replacement + sentence[marker + 2 :]
