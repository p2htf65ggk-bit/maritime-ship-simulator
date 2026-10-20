# SPDX-License-Identifier: MPL-2.0
"""Read-only offline AIS/mask workload against a real ENC catalogue."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
import time
try:
    import resource
except ImportError:
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from maritime_simulator.application.ais import AISRepository, WaterMaskPendingError
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask, _tile_coordinate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--targets', type=int, default=80)
    parser.add_argument('--range', type=float, default=5)
    parser.add_argument('--sim-minutes', type=int, default=10)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if not 1 <= args.sim_minutes <= 360:
        parser.error('--sim-minutes must be between 1 and 360')
    service = SimulatorService()
    own = replace(service.snapshot(), latitude_deg=40.705, longitude_deg=-74.026, sog_kn=10)
    with TemporaryDirectory(prefix='enc-runtime-') as directory:
        water = OSMNavigableWaterMask(ROOT / 'assets/geodata/osm-simplified-land-polygons-3857.zip',
                                     Path(directory), allow_network=False)
        water.set_s57(S57Store(args.directory))
        started = time.monotonic()
        checks = {True: 0, False: 0, None: 0}

        def checker(lat, lon):
            result = water.is_water(lat, lon)
            checks[result] += 1
            return result

        repository = AISRepository(generated_target_count=args.targets, active_range_nm=args.range,
            water_checker=checker, water_sampler=water.sample_water_position,
            water_preparer=water.prepare_ais_region)
        durations = []
        try:
            repository.use_generated(own)
            while len(repository.all_targets()) < args.targets and time.monotonic() - started < 40:
                step_started = time.perf_counter()
                repository.update_generated(own)
                durations.append((time.perf_counter() - step_started) * 1000)
                time.sleep(.02)
            filled_s = time.monotonic() - started
            initial = repository.all_targets()
            initial_tiles = {_tile_coordinate(t.latitude_deg, t.longitude_deg, 15)[0] for t in initial}
            water_at_creation = [checker(t.latitude_deg, t.longitude_deg) for t in initial]
            # Warm sparse synthetic positions on both sides of the harbour independently of generation.
            synthetic_waits = []
            for lat, lon in ((40.715, -74.021), (40.683, -74.045), (40.695, -74.012)):
                probe_started = time.monotonic()
                while True:
                    try:
                        repository.add_synthetic_target(own, latitude_deg=lat, longitude_deg=lon, speed_kn=3)
                        synthetic_waits.append(round(time.monotonic() - probe_started, 3))
                        break
                    except WaterMaskPendingError:
                        if time.monotonic() - probe_started > 8:
                            synthetic_waits.append('timeout')
                            break
                        time.sleep(.02)
                    except ValueError as error:
                        synthetic_waits.append(str(error))
                        break
            # Allow background workers to complete between accelerated simulation ticks.
            unknown_positions = stopped = 0
            motion_durations = []
            checks = {True: 0, False: 0, None: 0}
            memory_samples = []
            for second in range(1, args.sim_minutes * 60 + 1):
                step_started = time.perf_counter()
                repository.update_generated(replace(own, simulation_time_s=float(second)))
                motion_ms = (time.perf_counter() - step_started) * 1000
                durations.append(motion_ms)
                motion_durations.append(motion_ms)
                for target in repository.all_targets():
                    unknown_positions += checker(target.latitude_deg, target.longitude_deg) is None
                    stopped += target.sog_kn <= .01
                if second % 600 == 0:
                    assert len(water._enc_images) <= water.enc_tile_image_limit
                    assert len(water._enc_region_images) <= 512
                    if resource:
                        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                        memory_samples.append(dict(simulation_s=second,
                            peak_process_mib=round(peak / (1024**2 if sys.platform == 'darwin' else 1024), 2)))
                time.sleep(.005)
            unique_images = {id(image): image for image in
                             (*water._enc_region_images.values(), *water._enc_images.values())}
            result = dict(requested=args.targets, created=len(initial), fill_elapsed_s=round(filled_s, 2),
                region_tiles=len(water._enc_region_keys),
                region_ready=all(water._enc_region_complete(key) for key in water._enc_region_keys),
                region_failed_tiles=sum(isinstance(water._enc_region_missing.get(key), float) for key in water._enc_region_keys),
                region_cache_mib=round(sum(i.sizeInBytes() for i in water._enc_region_images.values()) / 1024**2, 2),
                unique_cached_pixel_mib=round(sum(i.sizeInBytes() for i in unique_images.values()) / 1024**2, 2),
                occupied_tiles=len(initial_tiles), all_initial_positions_water=all(v is True for v in water_at_creation),
                synthetic_wait_s=synthetic_waits, simulation_s=args.sim_minutes*60,
                memory_samples=memory_samples, unknown_position_checks=unknown_positions,
                stopped_target_samples=stopped, mask_checks={str(k): v for k,v in checks.items()},
                enc_cached_tiles=len(water._enc_images), enc_cache_mib=round(sum(i.sizeInBytes() for i in water._enc_images.values()) / 1024**2, 2),
                enc_pending=len(water._enc_pending), enc_error=water._enc_error,
                update_median_ms=round(statistics.median(durations), 2),
                update_p95_ms=round(sorted(durations)[int((len(durations)-1)*.95)], 2),
                update_max_ms=round(max(durations), 2), elapsed_s=round(time.monotonic() - started, 2))
            result.update(motion_median_ms=round(statistics.median(motion_durations), 2),
                          motion_p95_ms=round(sorted(motion_durations)[int((len(motion_durations)-1)*.95)], 2))
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(result))
        finally:
            water.close()
            service.close()


if __name__ == '__main__':
    main()
