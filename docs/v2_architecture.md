# Architecture - 2.3.2

## Ownership and data flow

```text
UI / validated external input
             |
          commands
             v
    SimulatorService / arbitration
             |
    one authoritative SimulationState
             |
       immutable snapshots
          /            \
sensor projection      UI / map / 3D payloads
      |
IEC encoders -> independent schedules -> UDP / TCP / serial
```

`domain/` defines the state and command contracts. `application/service.py`
coordinates ownership and fixed-step simulation. `simulation/` implements
clock, steering, environment, dynamics and routes. Protocol and transport layers
do not own the ship's navigation state. Noise changes projected sensor data,
not physical vessel motion.

## Navigation and compatibility modules

External Input arbitration chooses fresh main then fallback per axis; input-loss
actions unlock Manual, hold Freeze, or order Zero. Rudder XDR Order and Response
have independent endpoints/IDs with explicit degree/percent conversion. Complete
dual frames are selected atomically. Protocol parsing never owns the ship state.

Current code lives in `src/maritime_simulator/`. A deliberately small root `ui/`
package remains in use:

- `simulation.py` and `protocols.py`: inherited Track engine/data defaults behind
  `simulation/track_adapter.py`.
- `instrument_cluster.py`: presentation behind the V2 snapshot adapter.
- `widgets/map_widget.py`: Qt/WebChannel map transport and local map serving.
- `style.py`, `web_memory.py`, `webengine_profile.py`: shared display/lifecycle code.
- `app_metadata.py` and package initializers: application identity/import support.

There is no old application entry point in this snapshot. Do not treat the
shared modules as disposable legacy files. The 60-case Track fixture protects
their behavior without shipping previous application versions.

## AIS, output and faults

The AIS repository owns generated, live and synthetic targets and the common
display/output eligibility selection. Live position extrapolation is bounded
to 300 seconds by default; reception age is tracked separately. Generated and
synthetic traffic can use an independent Avoid Land classifier. Fault Injection
settings are saved but faults require an explicit activation after loading.

The protocol scheduler runs separately from UI painting. TCP clients have
bounded queues and reconnect ownership; serial input is event-driven on its
own Qt thread. UI shutdown closes timers, workers and local HTTP services.
Do not introduce blocking I/O or filesystem scans into refresh handlers.

## Maps and 3D

`visual/offline*.py`, `s57.py`, `s57_water.py` and `water_mask.py` implement
offline map packages, ENC selection and bounded land/water queries. S57
classification uses detailed chart geometry independently of the visible
map zoom/layer selection. It is not a depth-clearance certification.

`ui/web/map.html` and `s57_portrayal.js` draw the 2D view using bundled Leaflet.
`forward_view.py` manages one 3D page, either `google_maps_3d.html` or
`cesium.html`. Both consume simulator data; neither changes ship navigation.
No earlier Three.js/model-generation renderer or downloaded ship assets are
included. Hidden pages stop work and may be discarded/reloaded under memory
pressure. Cache budgets do not impose a hard process-wide Chromium RAM cap.

## Practical extension points

Add commands/state fields with service tests, extend IEC encoding in
`protocols/` with checksum/field tests, and add transport behavior in
`transport/` with loopback/lifecycle tests. Keep widget composition in `ui/`.
Larger controller and main-window modules remain candidates for gradual
test-backed decomposition; this snapshot does not claim a complete refactor.
