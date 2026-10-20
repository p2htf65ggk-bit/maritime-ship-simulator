# SPDX-License-Identifier: MPL-2.0
from dataclasses import replace
import math

import pytest

from maritime_simulator.application.ais import (
    AISRepository,
    AISTarget,
    _ais_maximum_turn_rate_deg_min,
    _avoidance_horizon_minutes,
    _signed_angle,
)
from maritime_simulator.application.service import SimulatorService


@pytest.mark.parametrize("water_result", [None, False])
def test_offline_generated_ais_bypasses_all_water_requests(water_result):
    own_ship = SimulatorService().snapshot()
    calls = []

    def checker(*args):
        calls.append(args)
        return water_result

    def sampler(*args):
        pytest.fail("Offline AIS must not request water-map samples")

    repository = AISRepository(
        generated_target_count=8, water_checker=checker, water_sampler=sampler,
        water_preparer=sampler, avoid_land=False,
    )
    repository.use_generated(own_ship)
    before = {target.mmsi: target for target in repository.all_targets()}
    assert len(before) == 8
    repository.update_generated(replace(own_ship, simulation_time_s=10.0))
    assert any(target.latitude_deg != before[target.mmsi].latitude_deg
               for target in repository.all_targets())
    assert calls == []


def test_generated_ais_waits_for_whole_water_region_then_tracks_position_and_range():
    own = SimulatorService().snapshot()
    prepared = False
    calls = []
    clock = [1.0]

    def prepare(*args):
        calls.append(args)
        return prepared

    repository = AISRepository(generated_target_count=4, active_range_nm=5,
                               water_checker=lambda *_: True, water_preparer=prepare,
                               monotonic=lambda: clock[0])
    repository.use_generated(own)
    assert calls == [(own.latitude_deg, own.longitude_deg, 5)]
    assert repository.all_targets() == ()
    prepared = True
    clock[0] += 1
    repository.update_generated(own)
    assert len(repository.all_targets()) == 4
    moved = replace(own, latitude_deg=own.latitude_deg + 1, simulation_time_s=1)
    clock[0] += 1
    repository.configure(active_range_nm=10)
    repository.update_generated(moved)
    assert calls[-1] == (moved.latitude_deg, moved.longitude_deg, 10)


def test_zero_generated_targets_do_not_prepare_water_area():
    own = SimulatorService().snapshot()
    repository = AISRepository(generated_target_count=0,
                               water_preparer=lambda *_: pytest.fail('No targets need no water region'))
    repository.use_generated(own)
    repository.update_generated(own)
    assert repository.all_targets() == ()


def test_offline_synthetic_ais_allows_creation_encounters_edits_and_movement():
    own_ship = SimulatorService().snapshot()

    def no_requests(*args):
        pytest.fail("Disabled land avoidance must never request water-map data")

    repository = AISRepository(water_checker=no_requests, water_sampler=no_requests,
                               avoid_land=False)
    repository.use_synthetic(own_ship)
    target = repository.add_synthetic_target(own_ship, speed_kn=12.0)
    repository.add_encounter(own_ship, encounter="head_on", target_speed_kn=10.0)
    edited = repository.update_synthetic_target(
        target.mmsi, latitude_deg=53.5, longitude_deg=10.0, course_deg=90.0,
    )
    repository.update_generated(replace(own_ship, simulation_time_s=60.0))
    moved = next(item for item in repository.all_targets() if item.mmsi == target.mmsi)
    assert moved.longitude_deg > edited.longitude_deg
    assert moved.sog_kn == 12.0


def test_disabling_avoid_land_releases_stopped_targets_and_reenables_validation():
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(water_checker=lambda *_: False)
    repository.use_synthetic(own_ship)
    target = repository.add_synthetic_target(own_ship, speed_kn=10.0, validate_water=False)
    repository.update_generated(replace(own_ship, simulation_time_s=1.0))
    repository.configure(avoid_land=False)
    assert repository.all_targets()[0].sog_kn == 10.0
    repository.update_synthetic_target(target.mmsi, longitude_deg=10.0)
    repository.configure(avoid_land=True)
    with pytest.raises(ValueError, match="land"):
        repository.update_synthetic_target(target.mmsi, longitude_deg=10.1)


def test_generated_ais_uses_simulation_time_not_wall_time():
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(generated_target_count=10, random_seed=7)
    repository.use_generated(own_ship)
    before = {target.mmsi: (target.latitude_deg, target.longitude_deg) for target in repository.all_targets()}
    repository.update_generated(own_ship)
    assert before == {target.mmsi: (target.latitude_deg, target.longitude_deg) for target in repository.all_targets()}

    repository.update_generated(replace(own_ship, simulation_time_s=60.0))
    after = {target.mmsi: (target.latitude_deg, target.longitude_deg) for target in repository.all_targets()}
    assert any(before[mmsi] != after[mmsi] for mmsi in before if mmsi in after)


def test_generated_ais_moves_forward_with_cog_within_five_degrees_of_heading():
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(
        generated_target_count=200,
        active_range_nm=100.0,
        random_seed=61162,
    )
    repository.use_generated(own_ship)

    before = {target.mmsi: target for target in repository.all_targets()}
    assert before
    for target in before.values():
        heading_cog_delta = (target.cog_deg - target.heading_deg + 180.0) % 360.0 - 180.0
        assert target.sog_kn >= 0.0
        assert abs(heading_cog_delta) <= 5.0

    repository.update_generated(replace(own_ship, simulation_time_s=60.0))
    after = {target.mmsi: target for target in repository.all_targets()}
    for mmsi, target in after.items():
        original = before[mmsi]
        travelled_nm = _test_distance_nm(
            original.latitude_deg,
            original.longitude_deg,
            target.latitude_deg,
            target.longitude_deg,
        )
        assert travelled_nm == pytest.approx(original.sog_kn / 60.0, abs=0.002)


def _test_distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    first = radians(lat1)
    second = radians(lat2)
    haversine = sin(dlat / 2.0) ** 2 + cos(first) * cos(second) * sin(dlon / 2.0) ** 2
    return 2.0 * 6371000.0 * asin(sqrt(haversine)) / 1852.0


def test_live_output_targets_are_exactly_the_green_active_map_targets():
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(generated_target_count=2, active_range_nm=5.0, map_range_nm=100.0)
    repository.ingest_live(
        (
            AISTarget(211000001, own_ship.latitude_deg, own_ship.longitude_deg, 0.0, 0.0, 0.0),
            AISTarget(211000002, own_ship.latitude_deg + 0.01, own_ship.longitude_deg, 0.0, 0.0, 0.0),
            AISTarget(211000003, own_ship.latitude_deg + 1.0, own_ship.longitude_deg, 0.0, 0.0, 0.0),
        ),
        replace_all=True,
    )
    map_targets = repository.map_targets(own_ship)
    output_targets = repository.output_targets(own_ship)
    assert output_targets == tuple(target for target in map_targets if target["active"])
    assert {target["mmsi"] for target in output_targets} == {211000001, 211000002}


def test_live_map_and_output_limits_are_deterministic_by_distance():
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(generated_target_count=1, active_range_nm=100.0, map_target_limit=2)
    repository.ingest_live(
        tuple(
            AISTarget(211000000 + index, own_ship.latitude_deg + index * 0.01, own_ship.longitude_deg, 0.0, 0.0, 0.0)
            for index in (3, 1, 2)
        ),
        replace_all=True,
    )
    map_targets = repository.map_targets(own_ship)
    assert [target["mmsi"] for target in map_targets] == [211000001, 211000002]
    assert [target["mmsi"] for target in repository.output_targets(own_ship)] == [211000001]

    repository.configure(target_count=0)
    assert repository.output_targets(own_ship) == ()
    assert not any(target["active"] for target in repository.map_targets(own_ship))


def test_sog_dependent_output_filter_is_strict_and_counts_only_eligible_targets():
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(
        generated_target_count=2,
        active_range_nm=100.0,
        map_range_nm=100.0,
        output_filter_mode="sog_dependent",
        output_min_sog_kn=0.5,
    )
    repository.ingest_live(
        tuple(
            AISTarget(
                211000100 + index,
                own_ship.latitude_deg + index * 0.001,
                own_ship.longitude_deg,
                sog,
                0.0,
                0.0,
            )
            for index, sog in enumerate((0.0, 0.5, 0.6, 1.2), start=1)
        ),
        replace_all=True,
    )

    mapped = repository.map_targets(own_ship)
    assert [target["active"] for target in mapped] == [False, False, True, True]
    assert [target["mmsi"] for target in repository.output_targets(own_ship)] == [
        211000103,
        211000104,
    ]

    repository.configure(output_filter_mode="all")
    assert [target["mmsi"] for target in repository.output_targets(own_ship)] == [
        211000101,
        211000102,
    ]


def test_live_target_dead_reckons_on_wall_clock_and_new_report_corrects_position():
    now = [100.0]
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(
        active_range_nm=100.0,
        map_range_nm=100.0,
        monotonic=lambda: now[0],
    )
    repository.ingest_live((
        AISTarget(
            211000010,
            own_ship.latitude_deg,
            own_ship.longitude_deg,
            60.0,
            90.0,
            90.0,
        ),
    ))
    received = repository.map_targets(own_ship)[0]

    now[0] += 60.0
    predicted = repository.map_targets(own_ship)[0]
    assert predicted["lon"] > received["lon"]
    assert predicted["lat"] == pytest.approx(received["lat"], abs=1e-4)
    assert repository.maximum_live_prediction_age_s() == pytest.approx(60.0)

    corrected_latitude = own_ship.latitude_deg + 0.01
    corrected_longitude = own_ship.longitude_deg - 0.01
    repository.ingest_live((
        AISTarget(
            211000010,
            corrected_latitude,
            corrected_longitude,
            12.0,
            180.0,
            180.0,
        ),
    ))
    corrected = repository.map_targets(own_ship)[0]
    assert corrected["lat"] == pytest.approx(corrected_latitude)
    assert corrected["lon"] == pytest.approx(corrected_longitude)
    assert repository.maximum_live_prediction_age_s() == 0.0


def test_live_target_prediction_is_capped_at_five_minutes():
    now = [100.0]
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(
        active_range_nm=100.0,
        map_range_nm=100.0,
        monotonic=lambda: now[0],
    )
    target = AISTarget(
        211000015,
        own_ship.latitude_deg,
        own_ship.longitude_deg,
        60.0,
        90.0,
        90.0,
    )
    repository.ingest_live((target,))

    now[0] += 420.0
    projected = repository.map_targets(own_ship)[0]

    assert repository.maximum_live_prediction_age_s() == pytest.approx(300.0)
    assert projected["dead_reckoning_age_s"] == pytest.approx(300.0)
    assert _test_distance_nm(
        target.latitude_deg,
        target.longitude_deg,
        projected["lat"],
        projected["lon"],
    ) == pytest.approx(5.0, abs=0.01)
    assert projected["dead_reckoning_active"] is False


def test_static_live_update_does_not_restart_motion_prediction_epoch():
    now = [50.0]
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(active_range_nm=100.0, monotonic=lambda: now[0])
    target = AISTarget(
        211000011,
        own_ship.latitude_deg,
        own_ship.longitude_deg,
        30.0,
        0.0,
        0.0,
    )
    repository.ingest_live((target,))
    now[0] += 20.0
    repository.ingest_live((replace(target, name="STATIC UPDATE"),), motion_update=False)
    now[0] += 20.0

    projected = repository.map_targets(own_ship)[0]
    assert projected["name"] == "STATIC UPDATE"
    assert repository.maximum_live_prediction_age_s() == pytest.approx(40.0)


def test_live_target_payload_distinguishes_provider_interval_from_dead_reckoning():
    now = [10.0]
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(active_range_nm=100.0, monotonic=lambda: now[0])
    target = AISTarget(211000012, own_ship.latitude_deg, own_ship.longitude_deg, 12.0, 90.0, 90.0)
    repository.ingest_live((target,))
    now[0] += 18.0
    repository.ingest_live((replace(target, longitude_deg=target.longitude_deg + 0.01),))
    now[0] += 7.0

    payload = repository.map_targets(own_ship)[0]
    assert payload["provider_report_age_s"] == pytest.approx(7.0)
    assert payload["provider_report_interval_s"] == pytest.approx(18.0)
    assert payload["dead_reckoning_age_s"] == pytest.approx(7.0)
    assert payload["dead_reckoning_active"] is True

    now[0] += 5.0
    repository.ingest_live((replace(target, name="ONLY STATIC"),), motion_update=False)
    unchanged = repository.map_targets(own_ship)[0]
    assert unchanged["provider_report_age_s"] == pytest.approx(12.0)
    assert unchanged["provider_report_interval_s"] == pytest.approx(18.0)


def test_live_report_diagnostics_identify_stale_moving_targets():
    now = [100.0]
    own_ship = SimulatorService().snapshot()
    repository = AISRepository(active_range_nm=100.0, monotonic=lambda: now[0])
    repository.ingest_live((
        AISTarget(211000013, own_ship.latitude_deg, own_ship.longitude_deg, 10.0, 0.0, 0.0),
        AISTarget(211000014, own_ship.latitude_deg, own_ship.longitude_deg, 0.0, 0.0, 0.0),
    ))
    now[0] += 61.0
    diagnostics = repository.live_report_diagnostics()
    assert diagnostics["moving_targets"] == 1
    assert diagnostics["stale_moving_targets"] == 1
    assert diagnostics["oldest_provider_report_age_s"] == pytest.approx(61.0)


def test_synthetic_targets_reject_land_and_restore_saved_identity_without_network():
    own_ship = SimulatorService().snapshot()
    water_boundary = own_ship.longitude_deg + 0.02
    repository = AISRepository(
        generated_target_count=10,
        water_checker=lambda _latitude, longitude: longitude <= water_boundary,
    )
    target = repository.add_synthetic_target(
        own_ship,
        latitude_deg=own_ship.latitude_deg,
        longitude_deg=own_ship.longitude_deg,
        ship_type="Tug",
        mmsi=211_123_456,
        callsign="DTEST",
        name="TEST TARGET",
        length_m=33.0,
        beam_m=9.0,
        draught_m=3.0,
    )
    assert target.callsign == "DTEST"
    assert target.length_m == pytest.approx(33.0)
    with pytest.raises(ValueError, match="on land"):
        repository.add_synthetic_target(
            own_ship,
            latitude_deg=own_ship.latitude_deg,
            longitude_deg=water_boundary + 0.01,
        )
    with pytest.raises(ValueError, match="on land"):
        repository.update_synthetic_target(
            target.mmsi,
            latitude_deg=own_ship.latitude_deg,
            longitude_deg=water_boundary + 0.01,
        )

    payload = repository.synthetic_payload()
    offline = AISRepository(water_checker=lambda _latitude, _longitude: None)
    offline.apply_synthetic_payload(payload, own_ship)
    restored = offline.synthetic_targets()[0]
    assert restored.mmsi == 211_123_456
    assert restored.name == "TEST TARGET"
    assert restored.callsign == "DTEST"
    assert restored.length_m == pytest.approx(33.0)


def test_synthetic_encounters_allow_multiple_targets_of_the_same_category():
    own_ship = replace(
        SimulatorService().snapshot(),
        sog_kn=10.0,
        cog_deg=0.0,
        heading_deg=0.0,
    )
    repository = AISRepository(water_checker=lambda _latitude, _longitude: True)
    first = repository.add_encounter(
        own_ship,
        encounter="crossing_from_starboard",
        cpa_nm=0.3,
        tcpa_min=12.0,
        target_speed_kn=11.0,
    )
    second = repository.add_encounter(
        own_ship,
        encounter="crossing_from_starboard",
        cpa_nm=0.6,
        tcpa_min=18.0,
        target_speed_kn=13.0,
    )
    assert first.mmsi != second.mmsi
    assert first.name != second.name
    assert len(repository.synthetic_targets()) == 2


def test_own_ship_overtakes_places_slower_target_ahead():
    own_ship = replace(
        SimulatorService().snapshot(),
        sog_kn=10.0,
        cog_deg=0.0,
        heading_deg=0.0,
    )
    repository = AISRepository(water_checker=lambda _latitude, _longitude: True)

    target = repository.add_encounter(
        own_ship,
        encounter="own_ship_overtakes",
        cpa_nm=0.2,
        tcpa_min=10.0,
        target_speed_kn=8.0,
    )

    assert target.sog_kn == pytest.approx(8.0)
    assert target.cog_deg == pytest.approx(own_ship.cog_deg)
    assert target.latitude_deg > own_ship.latitude_deg
    assert abs(target.longitude_deg - own_ship.longitude_deg) < 0.01


def test_overtaking_encounters_reject_reversed_speed_relationships():
    own_ship = replace(
        SimulatorService().snapshot(),
        sog_kn=10.0,
        cog_deg=0.0,
        heading_deg=0.0,
    )
    repository = AISRepository(water_checker=lambda _latitude, _longitude: True)

    with pytest.raises(ValueError, match="lower than own-ship speed"):
        repository.add_encounter(
            own_ship,
            encounter="own_ship_overtakes",
            target_speed_kn=12.0,
        )
    with pytest.raises(ValueError, match="higher than own-ship speed"):
        repository.add_encounter(
            own_ship,
            encounter="overtaking_own_ship",
            target_speed_kn=8.0,
        )


@pytest.mark.parametrize("own_course_deg", [0.0, 73.0, 241.0])
@pytest.mark.parametrize("own_speed_kn", [3.0, 10.0, 24.0])
@pytest.mark.parametrize(("cpa_nm", "tcpa_min"), [(0.3, 12.0), (5.0, 0.5)])
@pytest.mark.parametrize(
    ("encounter", "expected_sector"),
    [
        ("head_on", "ahead"),
        ("crossing_from_starboard", "starboard"),
        ("crossing_from_port", "port"),
        ("overtaking_own_ship", "astern"),
        ("own_ship_overtakes", "ahead"),
        ("close_passing_port", "port_ahead"),
        ("close_passing_starboard", "starboard_ahead"),
    ],
)
def test_all_encounter_geometries_hold_sector_and_cpa_across_speeds(
    own_course_deg,
    own_speed_kn,
    cpa_nm,
    tcpa_min,
    encounter,
    expected_sector,
):
    own_ship = replace(
        SimulatorService().snapshot(),
        sog_kn=own_speed_kn,
        cog_deg=own_course_deg,
        heading_deg=own_course_deg,
    )
    if encounter == "overtaking_own_ship":
        target_speed_kn = own_speed_kn + 3.0
    elif encounter == "own_ship_overtakes":
        target_speed_kn = max(0.5, own_speed_kn - 2.0)
    else:
        target_speed_kn = max(0.5, own_speed_kn * 0.6)
    repository = AISRepository(water_checker=lambda _latitude, _longitude: True)

    target = repository.add_encounter(
        own_ship,
        encounter=encounter,
        cpa_nm=cpa_nm,
        tcpa_min=tcpa_min,
        target_speed_kn=target_speed_kn,
    )

    average_latitude = math.radians((own_ship.latitude_deg + target.latitude_deg) / 2.0)
    relative_east_nm = (
        (target.longitude_deg - own_ship.longitude_deg)
        * 60.0
        * math.cos(average_latitude)
    )
    relative_north_nm = (target.latitude_deg - own_ship.latitude_deg) * 60.0
    course = math.radians(own_course_deg)
    relative_right_nm = relative_east_nm * math.cos(course) - relative_north_nm * math.sin(course)
    relative_forward_nm = relative_east_nm * math.sin(course) + relative_north_nm * math.cos(course)

    if expected_sector in {"ahead", "port_ahead", "starboard_ahead"}:
        assert relative_forward_nm > 0.0
    elif expected_sector == "astern":
        assert relative_forward_nm < 0.0
    if expected_sector in {"starboard", "starboard_ahead"}:
        assert relative_right_nm > 0.0
    elif expected_sector in {"port", "port_ahead"}:
        assert relative_right_nm < 0.0

    target_course = math.radians(target.cog_deg)
    own_velocity = (
        own_speed_kn * math.sin(course),
        own_speed_kn * math.cos(course),
    )
    target_velocity = (
        target.sog_kn * math.sin(target_course),
        target.sog_kn * math.cos(target_course),
    )
    hours = tcpa_min / 60.0
    relative_at_cpa = (
        relative_east_nm + (target_velocity[0] - own_velocity[0]) * hours,
        relative_north_nm + (target_velocity[1] - own_velocity[1]) * hours,
    )
    assert math.hypot(*relative_at_cpa) == pytest.approx(cpa_nm, abs=0.015)


def test_generated_land_avoidance_turns_gradually_and_never_drops_target():
    own_ship = SimulatorService().snapshot()
    water_boundary = own_ship.longitude_deg + 0.02
    repository = AISRepository(
        generated_target_count=5,
        water_checker=lambda _latitude, longitude: longitude <= water_boundary,
    )
    target = repository.add_synthetic_target(
        own_ship,
        ship_type="Tug",
        latitude_deg=own_ship.latitude_deg,
        longitude_deg=own_ship.longitude_deg,
        course_deg=90.0,
        heading_deg=90.0,
        speed_kn=10.0,
    )
    repository.use_synthetic(own_ship)
    repository.update_generated(replace(own_ship, simulation_time_s=60.0))
    updated = repository.target(target.mmsi)
    assert updated is not None
    course_change = (updated.cog_deg - 90.0 + 180.0) % 360.0 - 180.0
    assert 0.0 < abs(updated.rot_deg_min) <= 60.0
    assert abs(course_change) == pytest.approx(abs(updated.rot_deg_min))
    assert 0.0 < updated.sog_kn < 10.0
    assert updated.longitude_deg <= water_boundary
    assert updated.heading_deg == pytest.approx(updated.cog_deg)

    for simulation_time_s in (90.0, 120.0, 150.0, 180.0):
        repository.update_generated(replace(own_ship, simulation_time_s=simulation_time_s))
    recovered = repository.target(target.mmsi)
    assert recovered is not None
    assert 0.0 <= recovered.sog_kn <= 10.0
    assert recovered.longitude_deg <= water_boundary


def test_generated_targets_start_with_a_water_safe_course():
    own_ship = SimulatorService().snapshot()
    boundary = own_ship.longitude_deg + 0.02
    checks = 0

    def water_checker(_latitude, longitude):
        nonlocal checks
        checks += 1
        return longitude <= boundary

    repository = AISRepository(
        generated_target_count=20,
        active_range_nm=4.0,
        water_checker=water_checker,
        water_sampler=lambda *_args: (own_ship.latitude_deg, own_ship.longitude_deg),
    )
    repository.use_generated(own_ship)

    targets = repository.all_targets()
    assert len(targets) == 20
    assert all(
        repository._course_is_water_safe(
            target,
            horizon_minutes=_avoidance_horizon_minutes(target),
        )
        for target in targets
    )
    assert checks < 5000


def test_generated_ais_turn_rate_scales_from_small_to_large_vessels():
    small = AISTarget(1, 0.0, 0.0, 10.0, 0.0, 0.0, length_m=10.0)
    medium = replace(small, mmsi=2, length_m=100.0)
    large = replace(small, mmsi=3, length_m=200.0)

    assert _ais_maximum_turn_rate_deg_min(small) == pytest.approx(60.0)
    assert 10.0 < _ais_maximum_turn_rate_deg_min(medium) < 60.0
    assert _ais_maximum_turn_rate_deg_min(large) == pytest.approx(10.0)


def test_unknown_distant_water_does_not_trigger_avoidance_or_slow_known_water():
    own = replace(SimulatorService().snapshot(), latitude_deg=0.0, longitude_deg=0.0)
    repository = AISRepository(water_checker=lambda lat, lon: True if abs(lat) < .01 and abs(lon) < .01 else None)
    target = repository.add_synthetic_target(own, latitude_deg=0.0, longitude_deg=0.0,
        course_deg=90, heading_deg=90, speed_kn=10)
    repository.use_synthetic(own)
    assert repository._course_is_water_safe(target, horizon_minutes=10) is None
    for second in range(1, 61):
        repository.update_generated(replace(own, simulation_time_s=float(second)))
        current = repository.target(target.mmsi)
        assert current.cog_deg == 90
        assert current.sog_kn == 10
        assert current.rot_deg_min == 0
    assert current.longitude_deg > 0
    assert not repository._avoidance_courses


def test_generation_accepts_known_departure_without_requiring_distant_loaded_tiles():
    own = replace(SimulatorService().snapshot(), latitude_deg=0.0, longitude_deg=0.0)
    repository = AISRepository(water_checker=lambda lat, lon: True if abs(lat) < .005 and abs(lon) < .005 else None)
    target = AISTarget(244100001, 0, 0, 10, 90, 90, length_m=50, beam_m=10)
    assert repository._course_is_water_safe(target, horizon_minutes=10) is None
    assert repository._find_water_safe_course(target, horizon_minutes=10,
        require_full_clearance=True, allow_pending_horizon=True) == 90
    repository._water_checker = lambda lat, lon: lon < .0001
    assert not repository._departure_is_water_safe(target)
    assert repository._course_is_water_safe(target, horizon_minutes=10) is False


def test_generated_ais_begins_avoidance_early_with_gradual_speed_reduction():
    own_ship = replace(SimulatorService().snapshot(), latitude_deg=0.0, longitude_deg=0.0)
    repository = AISRepository(
        water_checker=lambda _latitude, longitude: longitude <= 0.02,
    )
    target = repository.add_synthetic_target(
        own_ship,
        ship_type="Tug",
        latitude_deg=0.0,
        longitude_deg=0.0,
        course_deg=90.0,
        heading_deg=90.0,
        speed_kn=10.0,
    )
    repository.use_synthetic(own_ship)

    repository.update_generated(replace(own_ship, simulation_time_s=1.0))
    updated = repository.target(target.mmsi)

    assert updated is not None
    assert 0.0 < updated.sog_kn < 10.0
    assert 0.0 < abs(updated.rot_deg_min) <= 60.0
    assert abs((updated.cog_deg - 90.0 + 180.0) % 360.0 - 180.0) < 1.0
    assert updated.longitude_deg > 0.0


def test_generated_ais_selects_the_better_port_or_starboard_escape():
    own_ship = replace(SimulatorService().snapshot(), latitude_deg=0.0, longitude_deg=0.0)
    repository = AISRepository(
        water_checker=lambda latitude, longitude: latitude <= 0.01 and longitude <= 0.0,
    )
    target = AISTarget(
        244100001,
        0.0,
        0.0,
        10.0,
        0.0,
        0.0,
        length_m=75.0,
    )

    selected = repository._find_water_safe_course(
        target,
        horizon_minutes=_avoidance_horizon_minutes(target),
        require_full_clearance=False,
    )

    assert selected is not None
    assert ((selected - target.cog_deg + 180.0) % 360.0 - 180.0) < 0.0


def test_generated_ais_uses_small_safe_corrections_before_large_turns():
    repository = AISRepository(random_seed=12)
    target = AISTarget(
        244100050,
        0.0,
        0.0,
        10.0,
        90.0,
        90.0,
        length_m=65.0,
    )
    repository._course_water_clearance = lambda candidate, **_kwargs: (
        1.0 if abs(_signed_angle(candidate.cog_deg - 95.0)) < 0.1 else 0.2
    )

    selected = repository._find_water_safe_course(
        target,
        horizon_minutes=8.0,
        require_full_clearance=False,
    )

    assert selected == pytest.approx(95.0)


def test_generated_ais_emergency_fallback_does_not_reverse_or_oscillate():
    own_ship = replace(SimulatorService().snapshot(), latitude_deg=0.0, longitude_deg=0.0)

    def isolated_water(latitude, longitude):
        return abs(latitude) < 0.00001 and abs(longitude) < 0.00001

    repository = AISRepository(water_checker=isolated_water, random_seed=61162)
    target = repository.add_synthetic_target(
        own_ship,
        ship_type="Tug",
        latitude_deg=0.0,
        longitude_deg=0.0,
        course_deg=90.0,
        heading_deg=90.0,
        speed_kn=10.0,
    )
    repository.use_synthetic(own_ship)
    repository.update_generated(replace(own_ship, simulation_time_s=1.0))

    first_desired = repository._avoidance_courses[target.mmsi]
    first_offset = abs(_signed_angle(first_desired - 90.0))
    assert 25.0 <= first_offset <= 70.0
    assert first_offset < 90.0

    for simulation_time_s in range(2, 40):
        repository.update_generated(
            replace(own_ship, simulation_time_s=float(simulation_time_s))
        )
    assert repository._avoidance_courses[target.mmsi] == pytest.approx(first_desired)
    assert 0.0 <= abs(repository.target(target.mmsi).rot_deg_min) <= 60.0
