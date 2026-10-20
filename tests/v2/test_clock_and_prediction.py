# SPDX-License-Identifier: MPL-2.0
from maritime_simulator.simulation.clock import SimulationClock
from maritime_simulator.simulation.prediction import predict_six_minute_path


def test_clock_uses_deterministic_steps_and_scales_only_simulation_time():
    clock = SimulationClock(fixed_step_s=0.1, time_scale=1.0)
    normal = clock.advance_wall_time(0.2)
    assert normal.simulation_elapsed_s == 0.2
    assert normal.fixed_steps_s == (0.1, 0.1)

    clock.set_time_scale(10.0)
    accelerated = clock.advance_wall_time(0.2)
    assert accelerated.simulation_elapsed_s == 2.0
    assert len(accelerated.fixed_steps_s) == 20
    assert all(step == 0.1 for step in accelerated.fixed_steps_s)


def test_prediction_is_always_six_simulation_minutes():
    path = predict_six_minute_path(52.0, 3.0, 90.0, 12.0, 8.0)
    assert path[0].simulation_offset_s == 0.0
    assert path[-1].simulation_offset_s == 360.0

    straight = predict_six_minute_path(52.0, 3.0, 90.0, 12.0, 0.0)
    assert straight[-1].simulation_offset_s == 360.0
    assert straight[-1].longitude_deg > straight[0].longitude_deg
