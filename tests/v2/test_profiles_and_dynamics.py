# SPDX-License-Identifier: MPL-2.0
import json
import math
from dataclasses import replace

import pytest

from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.profiles import VesselProfileStore
from maritime_simulator.domain.state import SimulationState
from maritime_simulator.simulation.dynamics import VesselDynamics


def test_factory_profiles_are_immutable_and_cover_all_v1_models():
    store = VesselProfileStore()
    assert {profile.display_name for profile in store.factory_profiles.values()} == {
        "Crew Transfer Vessel",
        "Container Feeder",
        "15k TEU Container",
        "24k TEU Container",
        "VLCC",
    }
    with pytest.raises(TypeError):
        store.factory_profiles["new"] = object()
    with pytest.raises(TypeError):
        store.get("crew-transfer-vessel").track_parameters["track_pid_kp"] = 999.0


def test_user_profile_can_be_renamed_and_reset_without_mutating_factory(tmp_path):
    path = tmp_path / "profiles.json"
    store = VesselProfileStore(path)
    factory_speed = store.get("crew-transfer-vessel").max_speed_kn
    created = store.create_user_profile(
        "crew-transfer-vessel",
        "training-ctv",
        "Training CTV",
        max_speed_kn=28.0,
    )
    assert created.max_speed_kn == 28.0
    assert store.get("crew-transfer-vessel").max_speed_kn == factory_speed
    assert store.rename_user_profile("training-ctv", "Renamed CTV").display_name == "Renamed CTV"
    reset = store.reset_user_profile("training-ctv")
    assert reset.max_speed_kn == factory_speed
    assert json.loads(path.read_text(encoding="utf-8"))[0]["profile_id"] == "training-ctv"


def test_builtin_profile_can_be_overridden_serialized_and_reset(tmp_path):
    path = tmp_path / "profiles.json"
    store = VesselProfileStore(path)
    original = store.get("crew-transfer-vessel")
    store.update_profile(
        original.profile_id,
        display_name="Harbour CTV",
        max_speed_kn=31.5,
    )
    assert store.is_factory_profile(original.profile_id)
    assert store.get(original.profile_id).display_name == "Harbour CTV"
    assert len([item for item in store.all_profiles() if item.profile_id == original.profile_id]) == 1

    restored = VesselProfileStore(tmp_path / "restored.json")
    restored.apply_payload(store.to_payload())
    assert restored.get(original.profile_id).max_speed_kn == pytest.approx(31.5)
    reset = restored.reset_user_profile(original.profile_id)
    assert reset.display_name == original.display_name
    assert reset.max_speed_kn == original.max_speed_kn


def test_builtin_profile_cannot_be_deleted(tmp_path):
    store = VesselProfileStore(tmp_path / "profiles.json")
    with pytest.raises(ValueError, match="cannot be deleted"):
        store.delete_user_profile("crew-transfer-vessel")


def test_vessel_list_keeps_operator_factory_order_then_size_sorts_custom_profiles(tmp_path):
    store = VesselProfileStore(tmp_path / "profiles.json")
    store.create_user_profile(
        "container-feeder",
        "compact-harbour-vessel",
        "Compact Harbour Vessel",
        length_m=18.0,
        beam_m=5.0,
        max_rot_deg_min=24.0,
    )
    profiles = store.all_profiles()
    assert [profile.profile_id for profile in profiles[:5]] == [
        "crew-transfer-vessel",
        "container-feeder",
        "15k-teu-container",
        "24k-teu-container",
        "vlcc",
    ]
    assert profiles[5].profile_id == "compact-harbour-vessel"


def test_calculated_track_values_form_a_complete_conservative_factory_interpolation(tmp_path):
    store = VesselProfileStore(tmp_path / "profiles.json")
    store.create_user_profile(
        "container-feeder",
        "custom-test-vessel",
        "Custom Test Vessel",
        length_m=145.0,
        max_speed_kn=19.0,
        max_rot_deg_min=16.0,
        rot_time_constant_s=22.0,
    )
    estimated = store.calculate_track_parameters("custom-test-vessel")
    references = tuple(store.factory_profiles.values())
    common_keys = set.intersection(*(set(profile.track_parameters) for profile in references))
    assert set(estimated) == common_keys
    for key, value in estimated.items():
        factory_values = [float(profile.track_parameters[key]) for profile in references]
        assert math.isfinite(value)
        assert min(factory_values) <= value <= max(factory_values)


def test_calculated_track_values_preserve_an_exact_factory_profile():
    store = VesselProfileStore()
    expected = store.factory_profiles["crew-transfer-vessel"].track_parameters
    assert dict(store.calculate_track_parameters("crew-transfer-vessel")) == dict(expected)


def test_user_profile_cannot_override_identity_or_create_invalid_dynamics(tmp_path):
    store = VesselProfileStore(tmp_path / "profiles.json")
    with pytest.raises(ValueError, match="Unsupported"):
        store.create_user_profile(
            "crew-transfer-vessel",
            "bad-id",
            "Bad Identity",
            factory_profile_id="stolen-id",
        )
    with pytest.raises(ValueError, match="positive"):
        store.create_user_profile(
            "crew-transfer-vessel",
            "bad-speed",
            "Bad Speed",
            max_speed_kn=0.0,
        )


def test_all_profiles_cross_hardover_in_no_more_than_30_seconds():
    store = VesselProfileStore()
    dynamics = VesselDynamics()
    for profile in store.factory_profiles.values():
        state = SimulationState(vessel_profile_id=profile.profile_id, control_mode=ControlMode.SHIP)
        state.steering.response_starboard_deg = -profile.max_rudder_deg
        state.steering.order_starboard_deg = profile.max_rudder_deg
        state.engine.effective_speed_order_kn = 0.0
        for _ in range(300):
            dynamics.integrate(state, profile, 0.1)
        assert state.steering.response_starboard_deg == pytest.approx(profile.max_rudder_deg), profile.display_name


def test_astern_speed_has_negative_sog_and_moves_behind_heading():
    store = VesselProfileStore()
    profile = store.get("crew-transfer-vessel")
    state = SimulationState(vessel_profile_id=profile.profile_id, control_mode=ControlMode.SHIP)
    state.motion.heading_deg = 90.0
    state.motion.stw_kn = -6.0
    state.engine.effective_speed_order_kn = -6.0
    longitude = state.position.longitude_deg
    VesselDynamics().integrate(state, profile, 1.0)
    assert state.motion.sog_kn < 0.0
    assert state.position.longitude_deg < longitude


def test_environment_generates_true_wind_and_aft_transversal_speed():
    store = VesselProfileStore()
    profile = store.get("container-feeder")
    state = SimulationState(vessel_profile_id=profile.profile_id, control_mode=ControlMode.SHIP)
    state.motion.heading_deg = 20.0
    state.motion.stw_kn = 12.0
    state.motion.rate_of_turn_deg_min = 8.0
    state.environment.wind_from_deg = 120.0
    state.environment.wind_speed_kn = 30.0
    state.environment.current_from_deg = 270.0
    state.environment.current_speed_kn = 2.0
    state.engine.effective_speed_order_kn = 12.0
    VesselDynamics().integrate(state, profile, 0.2)
    assert state.environment.true_wind_from_deg == pytest.approx(120.0)
    assert state.environment.true_wind_speed_kn == pytest.approx(30.0)
    assert state.motion.aft_transverse_ground_kn != pytest.approx(state.motion.forward_transverse_ground_kn)


def test_rudder_effectiveness_one_preserves_linear_response_and_higher_is_more_aggressive():
    base = VesselProfileStore().get("crew-transfer-vessel")

    def resulting_rot(effectiveness: float) -> float:
        profile = replace(base, rudder_effectiveness=effectiveness)
        state = SimulationState(vessel_profile_id=profile.profile_id, control_mode=ControlMode.SHIP)
        state.motion.stw_kn = profile.max_speed_kn
        state.engine.effective_speed_order_kn = profile.max_speed_kn
        state.steering.order_starboard_deg = profile.max_rudder_deg * 0.25
        state.steering.response_starboard_deg = profile.max_rudder_deg * 0.25
        VesselDynamics().integrate(state, profile, 1.0)
        return state.motion.rate_of_turn_deg_min

    linear = resulting_rot(1.0)
    assert linear == pytest.approx(base.max_rot_deg_min * 0.25 / base.rot_time_constant_s)
    assert resulting_rot(2.0) > linear
    assert resulting_rot(0.5) < linear
