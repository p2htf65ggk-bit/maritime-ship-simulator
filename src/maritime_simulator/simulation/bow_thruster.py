# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math

from maritime_simulator.domain.profiles import VesselProfile
from maritime_simulator.domain.state import SimulationState


def integrate_bow_thruster(state: SimulationState, profile: VesselProfile, dt_s: float) -> tuple[float, float]:
    """Return added target ROT (deg/min) and lateral speed (kn) at the pivot.

    Reduced-order manoeuvring model: squared signed RPM, a smooth STW loss
    curve, estimated displacement/added mass and a bow moment arm. No CFD claim.
    """
    thruster = state.bow_thruster
    if profile.bow_thruster_power_kw <= 0.0:
        thruster.order_percent = thruster.response_percent = thruster.force_kn = 0.0
        thruster.user_order_percent = 0.0
        return 0.0, 0.0
    # Rate-limited motor response also traverses zero during reversals.
    step = 100.0 * dt_s / profile.bow_thruster_ramp_s
    difference = thruster.order_percent - thruster.response_percent
    thruster.response_percent += max(-step, min(step, difference))
    ratio = abs(state.motion.stw_kn) / profile.bow_thruster_half_effect_speed_kn
    thruster.efficiency = 1.0 / (1.0 + ratio**4)
    rpm = thruster.response_percent / 100.0
    thruster.force_kn = profile.bow_thruster_power_kw * profile.bow_thruster_thrust_kn_per_kw * rpm * abs(rpm) * thruster.efficiency
    mass_kg = profile.effective_displacement_t * 1000.0
    force_n = thruster.force_kn * 1000.0
    lever_m = profile.length_m * (profile.pivot_fraction_from_bow - profile.bow_thruster_fraction_from_bow)
    inertia = mass_kg * (profile.length_m * 0.25)**2 * 1.2
    target_rot = math.degrees(force_n * lever_m / inertia) * profile.rot_time_constant_s * 60.0
    target_sway = force_n / (mass_kg * 1.5) * profile.sway_time_constant_s / 0.514444
    return target_rot, target_sway
