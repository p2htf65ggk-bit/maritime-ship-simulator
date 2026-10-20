# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from maritime_simulator.domain.profiles import VesselProfile
from maritime_simulator.domain.state import SteeringState


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def approach(current: float, target: float, maximum_delta: float) -> float:
    error = target - current
    if abs(error) <= maximum_delta:
        return target
    return current + (maximum_delta if error > 0.0 else -maximum_delta)


def update_steering(steering: SteeringState, profile: VesselProfile, dt_s: float) -> None:
    maximum = profile.max_rudder_deg
    maximum_step = profile.effective_rudder_rate_deg_s * max(0.0, dt_s)

    steering.order_starboard_deg = clamp(steering.order_starboard_deg, -maximum, maximum)
    steering.response_starboard_deg = clamp(
        approach(steering.response_starboard_deg, steering.order_starboard_deg, maximum_step),
        -maximum,
        maximum,
    )

    if steering.dual_rudder:
        steering.order_port_deg = clamp(steering.order_port_deg, -maximum, maximum)
        steering.response_port_deg = clamp(
            approach(steering.response_port_deg, steering.order_port_deg, maximum_step),
            -maximum,
            maximum,
        )
    else:
        steering.order_port_deg = steering.order_starboard_deg
        steering.response_port_deg = steering.response_starboard_deg
