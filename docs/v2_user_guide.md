# Maritime Ship Simulator V2 User Guide

This guide describes V2 `2.3.2`. It includes Generic XDR control inputs with an empty Unit, chart-based depth, online wind and current, tunnel bow-thruster control and independent external inputs, offline area downloads, the optional 3D Navigation View, Synthetic Traffic encounter setup and event-driven serial IEC input.

## Chart-Based Depth

Environment > Depth selects **Manual** or **Chart-based**. Chart-based depth first
uses enabled, locally installed S57 cells at own ship, regardless of the visible map
source, layers or zoom. If no usable local depth is available, online mode falls back
to numeric GEBCO data without an API key. If neither source is available, the last
effective depth is retained as the manual value. Offline Mode skips GEBCO entirely.
Neighbouring depth samples are interpolated; sparse S57 areas use their encoded
minimum depth. A local chart must contain usable depth data at that position.

The transducer is at keel level. The vessel model's editable **Draft** is subtracted
from chart water depth, with output limited to zero or greater. The resulting
depth is shared by the instruments and depth protocols. Manual depth means depth
below keel/transducer and is not reduced by Draft a second time. The input range
is 0-12,000 m.

If chart data are missing or fail to load, the **last effective depth automatically
becomes the manual fallback value**. This prevents an unnecessary jump to an older
saved value. Chart-based operation resumes automatically when data return. Before
the first reading, the saved manual depth is used. The source selector retains
Chart-based during fallback; the status beneath it shows the source actually in use.
Selecting Manual explicitly stops chart queries. Source and fallback value are
included in configuration exports and the Environment Start-up group.

Depth Noise remains available in either mode and does not feed back into the
stored baseline. GEBCO's coarse global grid cannot resolve every harbour channel.
The simulator applies no live tide correction, automatic grounding physics or
navigation-safety guarantees. Queries run asynchronously with bounded caches and
work limits; no whole-region bathymetry download is needed.

During normal travel, S57 depth is sampled after roughly 5 m of movement, at most
once per real second. At 10x acceleration the moving-sample interval is two seconds,
at 30x three seconds, and at 100x or more five seconds. The distance threshold grows
with accelerated travel, instead of triggering a query every 5 m. Stationary checks
remain every 30 seconds. Response validation allows expected motion during a bounded
query delay; an explicit position change or route-position seek discards old results.
High acceleration therefore uses coarser depth samples along the route.

GEBCO completes one four-point sample before moving to the latest position. Cached
data can still be interpolated at four updates per real second. Network latency can
reduce the delivered update rate; the last depth is held between samples. Sensor noise
and configured protocol transmission rates are independent of this sampling rate.

## Online Wind And Current

Environment > Wind, Current and Atmospheric data each use the same **Manual / Online - Open-Meteo**
source labels. Wind and Current sit side by side at the normal sidebar width, with each
speed slider above its numeric value. They stack vertically on very narrow panes.
Atmospheric values use a compact two-column grid. Sources may show the short caption
**Online** when space is limited; their menus retain **Online - Open-Meteo**.
Online uses current model data at own ship: 10 m wind and surface current.
Current direction is converted to the existing **Current From** display. The simulator
still calculates apparent wind and vessel drift from the same shared environment.
Atmospheric XDR values retain their separate source selector.

When data are missing or the application is offline, the last valid direction and speed
are held, and the controls unlock for manual adjustment. The selector retains Online
and resumes automatically when data return. Select Manual to stop automatic updates.
The active source is shown beneath each control group; its tooltip contains the feed
status. Sources and current fallback values are saved in Environment Start-up and exports.

The feeds are asynchronous, cached, and normally refresh every ten minutes or after
moving five nautical miles, with a minimum of one minute between requests per feed.
Current data are coarse (about 8 km), not a detailed harbour or river tidal-stream model.
Invalid or excessively distant marine grid cells are rejected. Use Manual for controlled
local-current test scenarios.

The **Open-Meteo access** icon beside Atmospheric data opens the shared optional API-key setting. Without a key the
free service is for non-commercial use only. Commercial use requires an appropriate
customer API key. See the provider's [terms](https://open-meteo.com/en/terms) and
[API access](https://open-meteo.com/en/pricing). Keys are included in configuration exports;
keep such files private. No key is built into the application.

## Bow Thruster And External Input

In model modes, the horizontal **Bow Thruster** slider below the telegraph sets
signed RPM demand: left/negative moves the bow to port, right/positive to
starboard. Double-click sets zero. The readout and Bow Thruster instrument show
order and actual RPM percentages; the instrument combines actual RPM in one
horizontal bar with a yellow order triangle. The motor takes time to accelerate or reverse.
Effectiveness falls smoothly with STW, including astern operation, independently
of current/SOG. Settings > Vessel Models > Bow Thruster contains power in kW/MW,
rated RPM, ramp time, loss curve and installation parameters. Zero power means
not fitted. These are representative demo models, not certified ship predictions.

Double-click a slider to reset it: rudder returns to midships, bow thrust to zero,
and engine telegraph to Stop. Controls without a valid zero return to their
configured default, bounded by the current limits. Disabled external controls
cannot be reset manually. Camera sliders reset bearing to zero, elevation to
12 degrees and range to the forward-view default, independently of each other.

**Settings > External Input** has Rudder, Bow Thruster and Engine Telegraph tabs.
Each group has three radio-button columns: **Main Control Source**, **Fallback
Control Source** (including None), and **On Input Loss** (Manual, Freeze or Zero).
Main may be Manual; external main and fallback must differ. Each protocol has
independent UDP/TCP/Serial endpoint settings. Bow thruster and telegraph can be external in
any model mode; rudder input is selected in External mode, while Ship keeps
manual steering and Track keeps its route controller. Manual rudder is also
available in External mode. Sensor Control remains a manual sensor test mode.

Each protocol's input lamp shows green for a valid value received within 5 s,
grey for a matching empty field, yellow after 5-30 s, and red after 30 s or before
any valid reception. These are input-age indications, not the 2.5 s control
freshness timeout. Inputs continue to be monitored when Manual is selected.

- TRC: thruster 1 by default, RPM demand; P = signed percentage, R = signed RPM
  converted with the vessel's configured rated RPM. Valid C/R flags are accepted
  as incoming simulation control data. Pitch/azimuth do not steer the tunnel.
- PRC: engine/shaft 1 by default, valid (A) lever demand position -100 to +100%;
  positive is ahead. Intermediate orders remain continuous. The percentage maps
  to telegraph lever position through its existing detents, not maximum-speed percentage.
- XDR: exact Type/Unit/ID match, default A/P and BowThrust#0 or EngTele#0;
  legacy G/P and G with a null (empty) Unit remain selectable. Restricted Type/Unit
  dropdowns offer only supported percent inputs (-100 to +100%). A null Unit is
  an empty field between commas, not the text `null`. It uses the same signed
  percent mapping as P. Type/Unit/ID matching stays exact, so choose `(null field)`
  explicitly for such a sender. Saved empty units and G/P filters are preserved.
  Other measurements in the same sentence are ignored. Advanced output can
  generate A/P, G/P and G/null-unit test messages.
- Rudder XDR Order/Response: A/D carries degrees by default; A/P, legacy G/P or G/null Unit
  carries signed percent, mapped to the selected vessel's maximum rudder angle.
  IDs default to RudderOrder#0 and Rudder#0. An optional PORT ID enables dual
  rudder input; both values must occur together in one valid sentence.

External controls move with received commands but are disabled for manual edits.
Priority changes after 2.5 seconds without a valid preferred sample, and returns
on its first valid sample. If neither is current, **Manual** unlocks the control
(rudder/bow demand starts at zero, engine retains its last order). **Freeze**
holds the last command or measured rudder response; **Zero** orders zero, allowing
the modelled motor/steering response to ramp down. Freeze/Zero keep manual edits
locked. Freeze does not pause the simulation. Fresh valid input takes control again unless Manual
is selected. An external engine policy overrides planned speeds; route-end Stop
still takes precedence. Invalid/checksum/non-finite/out-of-range values do not
renew freshness or change the command. Settings and vessel parameters persist
in configuration exports and the corresponding Fixed/Last Saved Start-up groups.

The **API KEY** button in the 3D view remains available even when no Google key is
configured or the provider fails to load. Use it to enter or correct the key and
save; the selected 3D provider then reloads with the new settings.

Simulation acceleration is selected in the upper-left status area beside the marine DDM position display.

## Start

```bash
venv/bin/python run_v2.py
```

## Controls

- **Sensor Control** publishes exact manual position and motion values. HDG and COG use interactive compass dials. `COG follows HDG` retains their offset; `ROT drives HDG` explicitly enables heading integration. Rudder and other optional sensors are in **Other**.
- **Ship Control** runs the selected vessel model. The vertical telegraph has Full Ahead at the top, accepts continuous orders between detents and keeps the surrounding one or two stages bold. Every detent is directly clickable. Enable **Keyboard control** for Up/Down telegraph steps and one-degree Left/Right rudder commands.
- **Track Control** keeps the manual telegraph unless **Use planned speeds** is enabled. The controller owns rudder order. Operating Turn Rate or Turn Radius makes that value fixed; the other value is calculated from current speed and shown dimmed while remaining directly operable. Changes are coalesced while dragged, so route geometry is not rebuilt for every slider pixel. Controller phases use operator labels such as **Inbound Straight** and **Planned Turn**.
- **External** uses ROR or XDR Order before the steering gear, or RSA or XDR Response as measured rudder response. Select main, optional fallback and loss action in External Input. PORT and STBD always come from one complete source frame. Engine and bow sources remain independently selectable.
- **Simulation Time** changes simulation time only. Protocol output rates remain wall-clock rates and map prediction remains six simulated minutes. Any value above `1x` is highlighted red in the status strip.

The left panel has separate **Control**, **Environment**, **AIS**, **Noise** and **Other**
tabs and scrolls on small screens. Wind and current use circular compass controls.
**Atmospheric data** owns the four standard XDR environment values: air temperature
in Celsius, relative humidity, dew point and surface pressure. Select editable
**Manual** or **Online - Open-Meteo**. Online mode uses the current
own-ship latitude/longitude, refreshes immediately after a significant position jump
and otherwise at a bounded interval, and needs no API key. A network/provider error
retains the last XDR values and never blocks simulation or output. The source and
values are stored in complete configurations and the Environment Start-up group.
MDA/MTW values remain separate and manual under Other. Other also contains dual-rudder,
response/RSA, transversal speeds and propulsion/thruster values. Protocol enable
checkboxes are deliberately not duplicated there: all UDP, TCP, Serial and additional-path
output states are controlled only in Settings. Manual model-derived values are disabled
outside Sensor Control. Noise is intentionally applied only to projected protocol output
and never feeds back into vessel dynamics. Every channel has its own amplitude and
**Cycle time** slider from `0.1` to `60.0 s`; the cycle defines one complete deterministic
noise oscillation. Linked wind direction and speed noise uses the same sample and cycle
for MWD true wind, MWV relative wind and MWV true-theoretical wind. Noise amplitudes and
cycle times are retained in complete configurations and in the Noise Start-up group.
The panel can be collapsed to narrow vertical key controls; Track mode shows the active
ROT or Radius control directly.

**Fault Injection** is placed directly behind Other. It applies realistic sensor and transmission failures after the vessel model. Available scenarios include GNSS jamming/intermittency/drift/spoofing, heading/speed/rudder/wind freezes, sensor biases, GNSS sentence loss, packet loss, checksum corruption and output latency. This changes instrument indications and protocol output but never the true vessel dynamics. Scenario selections can be saved in configurations and Start-up, but all faults are deliberately inactive after loading or restarting and must be enabled by the operator.

## Route And Map

The current or saved route is loaded at startup; otherwise `routes/default_route.rtz` is used. The route is shown automatically only in Track Control. Right-click the map to add/delete waypoints, set own-ship position or set heading. Waypoints are draggable and all changes pass through V2 commands. Manual position/route seek clears own-ship history.

Route Planning supports RTZ import, default route, add/remove/clear/reverse, direct cell editing, standard copy/paste, right-click **Apply to all**, planned/control-level speed, Stop/Restart/Reverse end actions and live route seek. Turn Rate and Turn Radius have separate columns. Entering or pasting one selects that fixed mode and shows **Calculated** in the other column. Speed, ROT and radius are displayed and entered with one decimal. Planned cells always remain numeric; missing imported values default to 10 kn and 10°/min. **Use planned speeds** and **Use planned Turn Rate / Radius** choose waypoint values. The associated manual controls are dimmed while planned values own the command; operating a dimmed manual control automatically clears its planned option and transfers ownership back to that control. The status identifies the next waypoint, leg bearing, elapsed/total active-leg distance and controller phase. The map draws controller-aligned Wheel Over Points and planned turn arcs. Overlapping geometry is highlighted in red without forcing radius or speed; adjacent conflicts also receive a marker at the averaged transition progress and one dashed compromise path. The Track Controller follows that same sampled path, keeps its path index monotonic and does not apply normal pre-WOP limiting again after transition guidance has produced the command. Vessel steering and ROT limits remain authoritative, so physically impossible geometry remains highlighted and is followed as closely as the model permits. The panel can collapse while retaining its status and route-position controls.

## AIS And Instruments

Generated, live and synthetic AIS share one repository. **Synthetic Traffic Generator** can hold multiple operator-defined targets with vessel type, relative bearing, range, course, heading and speed. **Add Encounter** creates repeatable Head-on, Crossing, Overtaking and Close Passing situations from editable CPA and TCPA. Crossing targets begin on the named side. **Overtaking Own Ship** requires a faster target and places it astern; **Own Ship Overtakes** requires a slower target and places it ahead. The dialog proposes a suitable target speed from current own-ship speed, while invalid overtaking speed relationships are rejected with a clear message. Right-click the 2D map to add a target at the selected water position. Synthetic targets can be dragged; dragging the vector handle changes course and heading together, while its length changes speed. Advanced identity permits MMSI, call sign and dimensions to be overridden. Synthetic targets and encounters are stored in complete configurations and the AIS Start-up group.

**Avoid Land** applies to both Generated and Synthetic AIS and is enabled by default. Disable it for offline operation or unrestricted scenarios: creation, encounters, map dragging and movement bypass all water-map checks and requests. Targets can then cross land. The choice is saved in configurations and the AIS Start-up group, and is disabled in Live mode because live targets are not land-filtered. Existing configurations without this setting retain the enabled default.

With Avoid Land enabled, generated and synthetic traffic uses the bundled OpenStreetMap coastline mask and an on-demand map-colour refinement for inland waterways. Generated targets are accepted only when their position and initial forward course have confirmed water clearance. A moving target evaluates both port and starboard escape paths before the shoreline, favours smaller corrections, and keeps the selected side stable instead of alternating or falling back to repeated 180-degree turns. Vessel size defines the available maximum ROT; stable per-target variation gives actual values from 0 degrees/minute on a steady course up to 60 degrees/minute for the smallest targets. Speed reduces progressively only as clearance becomes limited, can reach zero if no safe movement remains, and recovers progressively after a safe course is established. While an inland map tile is being resolved, the target remains at its last confirmed water position. Disabling Avoid Land releases avoidance-induced stops and restores the target's cruising speed; enabling it again resumes checks without relocating existing targets.

Generated and live AIS share one repository, so only active/green targets are eligible for VDM output. **AIS output filter** switches between **All** and **SOG dependent**. In SOG-dependent mode, only targets whose SOG is strictly greater than the configurable minimum are active; the prepared default threshold is `0.5 kn`, and the minimum field is disabled in All mode. The filter and threshold are stored in complete configurations and the AIS Start-up group. Generated targets always move ahead and keep COG within 5 degrees of their heading. Live target positions are advanced between provider reports from their latest SOG and COG and are corrected immediately when a new position report arrives. Prediction stops after 300 seconds without a new motion report so stale targets cannot continue indefinitely; provider targets are retained for up to six minutes without a fresh report. The live diagnostics distinguish the age and last interval of real provider reports from the current dead-reckoning age. The same values are shown for each target in its map popup. While the popup remains open, its dead-reckoning age is refreshed live as a whole number of seconds and kept on one line. Moving or seeking the own ship explicitly re-evaluates the local target set; aisstream.io receives a replacement geographic subscription over its existing connection. Replacement subscriptions are coalesced and limited to one per second. Normal own-ship movement renews that subscription after the vessel has moved a meaningful fraction of the selected range. BarentsWatch supplies a national stream, so its provider connection stays open while the local range filter is recentered.

Each due AIS cycle emits the target position report. AIS lines are paced across the cycle rather than sent as one UDP burst. A change to **Visible/output target limit**, active range or map eligibility immediately replaces pending paced VDM work with the current eligible target set without restarting any network transport. The limit applies to unique target MMSIs; static/voyage reports can add multiple lines for each eligible target. When live static/voyage data is available, the output also emits the IEC/AIS Type 5 two-fragment report containing available MMSI/IMO, callsign, vessel name, ship type, dimensions, draught, ETA and destination; multipart fragments stay close together while reports for different targets are staggered. Static data is repeated periodically and whenever its metadata changes. **Live AIS Settings** offers `aisstream.io` and Norwegian Coastal Administration data through BarentsWatch. aisstream.io uses an API key and sends the selected range or worldwide bounding box to the provider as the actual subscription. Its WebSocket transport negotiates `permessage-deflate`, reconnects automatically and reports whether compression was confirmed. TLS certificate validation remains enabled and combines the operating-system trust store with a bundled CA bundle, so portable builds do not depend on a separately installed Python certificate file. The provider stream is event-driven: the simulator cannot request an individual fast vessel more often, and the observed real-report interval is therefore the reliable indication of upstream update frequency. Worldwide mode can create very high traffic and CPU load. BarentsWatch requires an AIS client ID and secret and is geographically limited to Norwegian coverage by the provider. Its range is therefore a local own-ship filter; **Show all Norwegian coverage** disables only that local filter and is not a worldwide subscription. No provider credentials are bundled. Enter them in Live AIS Settings or supply `MARITIME_SIM_AISSTREAM_API_KEY`, `MARITIME_SIM_BARENTSWATCH_CLIENT_ID` and `MARITIME_SIM_BARENTSWATCH_CLIENT_SECRET`. Credentials are never printed in status or diagnostic text. Target count, active output range, map range and map limit remain directly adjustable. The status separates provider, transport, compression, subscription, received frames, discarded frames, valid reports, target count and real/predicted data age, so a connection without usable positions is not reported as working reception.

Under **Settings > Vessel Models > Track Controller**, **Calculate Values** creates a conservative baseline for a custom or modified vessel by interpolating the five tuned built-in profiles from vessel dimensions, speed and steering response. It is intended to make a new vessel steerable; final accuracy still needs representative track runs.

The **Dimensions & Speed** page controls hull dimensions, maximum ahead/astern speed and acceleration/deceleration. **Dynamics** controls maximum rudder, rudder travel rate, maximum ROT, ROT time constant, speed exponent, sway response and wind/current susceptibility. **Rudder effectiveness** is an abstract partial-rudder curve: `1.0` keeps the shipped linear response, values above `1.0` make partial rudder more effective, and values below `1.0` make it less effective. The configured full-rudder limit and maximum ROT remain authoritative. Track Controller fields tune straight-leg capture, integral bias, recovery angle, planned turns, counter-rudder and WOP timing; change them conservatively and validate the result on representative routes.

**Instruments** opens the graphical snapshot-only instrument set. Use **Customize** to add, remove and drag instruments. The selected layout is stored in V2 user configuration. Available tiles include heading, COG/SOG/STW, engine order, ROT, rudder, dual rudder, docking speeds, wind/current, depth, AIS and time. The ROT ring moves four times faster than in RC1 for easier recognition of low turn rates; the displayed ROT value is unchanged.

## 3D Navigation View

Use the compact **Map / 3D** selector above the main navigation area to switch between the route map and 3D Navigation View. **3D Window** moves the same renderer into a separate scalable window. Use the operating-system window controls to maximize or restore that window; `F11` and `Escape` remain available as keyboard alternatives. The detached 2D map uses its existing expand action in the same way. This is suitable for a dedicated second monitor and never creates a second WebEngine or tile stream. The 3D view consumes position, HDG, COG, SOG, ROT and the same AIS repository used by the map and protocol output. It is read-only and cannot steer the vessel.

Set a valid Google Maps Platform key through the small **API KEY** action in the lower-left corner, above map attribution, or provide `MARITIME_SIM_GOOGLE_3D_TILES_API_KEY` as an environment fallback. The same dialog selects **Auto (recommended)**, **Google 3D Maps (EEA compatible)** or **Photorealistic Tiles / Cesium**. Auto checks the direct Map Tiles endpoint, selects Cesium when available and switches to Google 3D Maps after a confirmed regional restriction. Saving Auto runs the check again. Enable Maps JavaScript API for Google 3D Maps and Map Tiles API for Cesium; the dialog links to both official setup pages. A key entered in the app is stored in local user configuration, never bundled or logged, but complete configuration exports intentionally include it together with AIS credentials and provider selection. Treat those JSON files as sensitive.

The same dialog provides **3D quality**. Keep **Auto (recommended)**. It watches available physical memory and can reduce the Cesium tile cache, temporary cache headroom, detail, render resolution and frame rate while another memory-heavy application or VM is active. On Google 3D Maps, whose tile cache remains provider-managed, it reduces simulator-overlay resolution, target/name/vector limits and refresh rate. Reductions happen immediately; quality returns gradually after memory has remained available for at least one minute. **Quality**, **Balanced** and **Low Memory** are persistent manual overrides.

Both providers show Google's static 3D scenery. Own ship and AIS targets are high-contrast, visually extruded simulator overlays whose hull length and beam come directly from the vessel profile or AIS dimensions. Display height is conservatively derived because AIS does not provide authoritative vessel height. Own ship is cyan with a white outline. AIS hulls eligible for protocol output are green; targets excluded by range, limit or the SOG filter are grey. Heading and predicted-path vectors use live simulator/AIS values with a three-minute horizon and remain visible over or through the scenery. Use **NAMES** to show or hide AIS labels without hiding target hulls, vectors or instruments. Old static ships visible inside Google imagery are background scenery, not live targets.

The semitransparent HUD has a compact top heading tape showing HDG, COG and ROT. Speed and Depth sit on the left; Wind and Current sit on the right, replacing the previous AIS list. Select **SOG/STW** for the main speed value, with the other reference below. A negative selected speed and **ASTERN** are red; ASTERN and the secondary speed use larger bold text, while AHEAD remains unchanged. Depth is **DBK**, below keel, with a bounded trace of received readings. Wind **T** is true FROM in degrees true; **A** is apparent wind relative to the bow. Current shows drift and **SET** toward degrees true. Both direction dials are bow-up. Narrow views use compact numeric instruments and can wrap speed footers without hiding values.

Pinch gestures always control map range, including when the pointer is over a HUD or text overlay; they do not zoom the WebView interface. The 2D map uses its stable centre as the zoom point so rapid gestures remain smooth and do not make the chart jump. With **Follow Own Ship** active, own ship is deliberately kept at that fixed zoom point. Native and Ctrl-wheel duplicates are discarded so one physical pinch produces one continuous map movement.

On the 2D map, the mouse wheel zooms and two-finger trackpad scrolling pans.
Alternatively hold both mouse buttons and move up to zoom in or down to zoom out.
Either button may be pressed first. A single right-click still opens map actions;
dragging editable targets, vectors and waypoints keeps its existing behavior.

The 3D renderer uses an opaque, hardware-accelerated WebGL surface and bounded 30-FPS visual updates. Protocol output remains on its own scheduler thread and is not stopped when the 3D view or Output Monitor is opened. On Windows the scheduler requests finer timer resolution and elevated worker priority while active; the Performance Monitor reports observed scheduler gaps for qualification.

Drag to look around through 360 degrees; right-drag, Shift-drag or use a two-finger trackpad swipe to pan without changing zoom. In Google 3D Maps the mouse wheel zooms directly; pinch remains available on a trackpad or touchscreen. A two-button mouse can also zoom by holding both buttons and moving vertically. Browser page zoom is locked to 100 percent, including in the embedded main-window view, so gestures over HUD text continue to control only the 3D camera. Camera-relative panning keeps a visible own ship at the same screen position during course changes. Heading and predicted-path lines start at the vessel waterline. Double-click or **FWD** restores elevation 9 degrees and a vessel-dependent range 20 percent closer than the previous preset. **3D** uses relative bearing -40 degrees, elevation 15 degrees and 1.4 times FWD range; **TOP** remains near-vertical. **CAM** starts off and reveals three side-by-side sliders for bearing, elevation and range. **HUD** toggles the instruments and tape. These controls sit at the bottom, above provider attribution. Explicit enabled controls and reference choices survive renderer reload. Provider-specific rendering details can differ slightly, but the simulator data, camera presets and overlay meanings remain the same.

This production view is not a certified navigation display. Google imagery can contain distorted water, static vessels or incomplete harbour geometry. See `docs/v2_1_forward_view.md` for architecture, API setup, performance limits, validation and known limitations.

## Output And Tools

- **Output** is a monitor only and displays lines actually accepted by the selected configured port. Opening or closing this window never restarts or pauses the output scheduler. Output scheduling runs on a dedicated wall-clock worker using the latest immutable simulation snapshot, so it does not wait for a long accelerated simulation step or UI operation. Every protocol keeps its own rate; serial load is shown where applicable.
- **Advanced** manages multiple independent validated IEC sentences and Free Text Playbacks. Every row has its own configured port, rate, Send Once/Cycle state and remove action. Use **+ Output Port** to create an additional persistent UDP, TCP or Serial endpoint that is not assigned to a regular sensor protocol. AUTO is offered only for reliable model values. Choice fields use descriptive lists and invalid combinations are rejected immediately. XDR provides grouped IEC 61162-1:2024 recommended identifiers and filters units by transducer type. Presets for air/water temperature, dew point, relative humidity, surface pressure, rudder, heel, pitch and roll use the live projected sensor value; all remaining recommended electrical, tank, machinery and flow identifiers remain available for manual values. Temperature presets use Celsius. In a recommended XDR ID, `#X` is the data-instance number (`0...9999`); presets start at `#0`, and the ID remains editable for additional same-type transducers. Active cycles continue when the window is hidden and are restored with the saved configuration.
- **Settings > Protocol Output** starts with one **UDP**, **TCP** and **Serial** tab. Use the **+** menu to add further independent paths of any transport, such as `UDP 2` or `Serial 2`; use **x** to remove the selected additional path. Added paths start with all protocols disabled and are stored in complete and automatic configurations. Every path gives each protocol its own On state, rate and endpoint, so the same data can be emitted through several UDP, TCP and Serial ports simultaneously at different rates. **XDR Environment** is a dedicated, default-off sensor section with four independently selectable and timed XDR rows: air temperature (`Air#0`), relative humidity (`Air#1`), dew point (`Air#2`) and surface pressure (`Baro#0`). The distinct IDs are shown directly in the protocol list. Sensor-row edits affect only the selected path and push to every protocol below that sensor; mixed child values aggregate as blank. TCP **Client** connects to the configured remote server and automatically retries after refused, reset, timed-out or half-open connections; bounded writes prevent a dead peer from holding the output worker indefinitely. TCP **Server** listens on the configured local bind address (`0.0.0.0` for all adapters, `127.0.0.1` for this computer only, or one local adapter address) and distributes each sentence to all connected clients using isolated bounded queues. Accepted clients use TCP keepalive and a low-latency dispatch cycle; a slow receiver cannot stall other clients or the UI. After a disconnect the server remains listening, but the remote radar/client must initiate its new connection. Clients connect to the simulator computer's reachable address and configured port, never to `0.0.0.0`. Serial tabs list ports currently reported by the operating system and remain editable for custom device paths. GNSS includes DTM with WGS 84 (`W84`) as both local and reference datum. AIS **VDM** emits configured visible traffic targets; AIS **VDO** emits the simulated own ship position and movement as an own-vessel Class A position report. VDO is disabled by default on every new path.
- **VBW** contains longitudinal and transverse water/ground speeds plus separate stern transverse water and ground speed/status pairs. Each configured **MWV** cycle sends both the relative (`R`) sentence and the true theoretical (`T`) sentence; the latter uses the vessel bow as angular reference and true wind speed. Both lines belong to the one configured MWV rate.
- **External Input - Rudder** gives ROR and RSA independent UDP/TCP/Serial endpoints and starts both automatically. Both sentence types are monitored continuously. Serial input is event-driven on a dedicated worker and does not synchronously wait in the UI thread; disconnects are retried automatically on Ubuntu, Windows and macOS. Select Manual, fixed ROR/RSA or one of the two priority policies; status shows the active source. With ROR active, ROR is the order and vessel steering dynamics supply response/RSA. With RSA active, RSA supplies physical response; a parallel valid ROR is used only for the Rudder Order indication, otherwise order is shown as 0 degrees. Status identifies detected ROR/RSA and Single/Dual topology. Four independent ROR PORT, ROR STBD, RSA PORT and RSA STBD lamps are green for an age of 0-5 seconds, yellow above 5 through 30 seconds, and red above 30 seconds or before first reception. Grey means a valid sentence was received but that channel field was empty; another valid field in the same sentence remains usable. **View Port** shows the exact received lines. A sentence carrying status `V` remains visible in the monitor but is deliberately rejected for model control.
- **Start-up** defines the unattended **Startup Setup** used by demo systems. Its table compares **Current**, **Last Saved** and **Fixed** values for every group and highlights the selected startup source. **Vessel Models** is an independent group containing the selected vessel plus all customized built-in and added model/controller parameters; **Mode** retains only operating mode and simulation time scale. Physical values use one decimal place; Position & Heading uses precise marine latitude/longitude notation. Per-row actions save Current as Last Saved or Fixed, while the two Save All actions capture every group without silently changing source selection. **Use Last Saved for All** and **Use Fixed for All** change all source selections explicitly. Last Saved changes only after **Close and Save** or an explicit save action. **Close without saving** restores the startup baseline without copying Fixed-overlaid values into Last Saved. Fixed values remain unchanged until an explicit Fixed capture. Position & Heading is independent from Control and Sensors; Route captures the route currently loaded in Route Planning. Active Advanced cycles restart only when active in the selected snapshot. Exported Startup Setup files contain source choices plus both saved snapshot sets and do not depend on the original RTZ path. Older Startup Setup formats are migrated automatically.
- **Save/Load Configuration** handles a complete operating snapshot and applies it immediately when loaded. It includes AIS credentials and the Google Map Tiles API key; the exported JSON must therefore be treated as sensitive. **Export/Import Startup Setup** handles future startup policy and saved startup snapshots. Changing a startup source or pressing one of its save actions is persisted immediately; no additional Settings save is required.
- **Vessel Models** exposes dimensions, dynamics and Track Controller parameters. The fixed operator order is Crew Transfer Vessel, Container Feeder, 15k TEU Container, 24k TEU Container and VLCC; custom profiles follow those built-in entries. Built-in vessels can be tuned and renamed but not deleted; each can be reset to shipped values. Custom vessels can be copied from any model, renamed and deleted. Complete JSON configurations include route, vessel profiles, all independent output paths, both external-input endpoints, AIS, Advanced Protocols, persistent Advanced ports and instrument layout and can be saved or loaded here.
- **Guide** opens the in-application guide. **About** contains program information and Performance Monitor.

For UDP input, **Bind address** is the local address the listener uses (`0.0.0.0` listens on all local IPv4 adapters). **Multicast group** is the multicast destination configured on the sender, such as `239.192.192.0`. **Interface IP** is the local computer's network-adapter address used to join that group; it is not the source device's IP. For UDP output, Auto mode derives unicast, broadcast or multicast from the destination address. Multicast **TTL** is a hop limit: each router reduces it by one, so `1` normally confines multicast packets to the local network. TTL does not change update rate, buffering or sentence lifetime and has no effect on unicast or broadcast output.

## Performance Monitor

Open **About → Performance Monitor** and enable collection. Health uses the latest 10 seconds and refreshes at a modest interval. UI response, simulation/Track timings, output-worker processing and recent transport errors use green/yellow/red bands. A high UDP/TCP sentence rate is not itself a bandwidth warning. Serial bandwidth has its own row, based on measured sentence bytes, framing and the active port's baud rate. AIS count and memory are grey informational rows. Current RAM includes WebEngine child processes and is sampled every five seconds; shared pages may be counted more than once. If unavailable, the main-process peak is shown instead. **Show technical details** includes available system RAM, swap activity where supported and local map-cache budgets. Historical peaks and total errors no longer keep the live health view red. Monitoring stays enabled until unchecked; closing the window only stops display refresh.

Local map, chart-geometry and water-image caches share an adaptive budget of 64, 96, 128 or 192 MiB, based on available system RAM. Reductions are immediate; increases require at least a minute of stable recovery. The active AIS water region remains protected. Under memory pressure, views hidden or minimized for at least a minute may unload their web document; reopening restores the camera/zoom and display choices, then receives current simulation/AIS data. Unfinished offline-area selection is retained instead of discarded. This does not automatically switch the active 3D view to 2D or change provider. Google manages its own active tile memory, so the local cache budget is not a hard limit on total app RAM.

**External Input - Rudder** separates **ROR - Rudder Order** and **RSA - Rudder Response** endpoint tabs. Each has **UDP**, **TCP** and **Serial** transport tabs; selecting a transport configures that listener. Merely switching between ROR and RSA does not restart reception. Fields irrelevant to the selected transport are hidden; multicast Interface IP is disabled when no group is configured. Bind-address tooltips distinguish this computer's listening address from a remote TCP server's address. The XTE status readout appears only in Track mode; the other readouts redistribute smoothly when modes change.

## Offline Maps

**Settings > Offline** separates **Protomaps** map-area management from **S57 / ENC** charts.
S57 accepts unencrypted `.000` base cells (with adjacent numbered updates), ZIP exchange sets,
or **Import Folder...** to find them recursively in a whole folder. SevenCs compiled `.7CB`/`.7CR`
charts are unsupported; use the original unencrypted `.000` exchange sets instead.
Up to 20,000 cells/250,000 source files are accepted, including a complete NOAA `All_ENCs.zip`.
Staging/import runs one cell at a time, with a 512 MiB limit per cell including updates, not per catalogue.
In folders containing an unpacked catalogue and its backup ZIP, unpacked cells take precedence;
select the ZIP explicitly if that is the edition you want. Completed cells survive cancellation/failure.
**Download NOAA...** provides a searchable official cell catalogue with multi-selection and background
download/import. **Select All Visible** affects only the search-filtered cells; hidden selections remain.
The complete visible catalogue can be selected; the worker downloads/imports one cell at a time.
Completed cells are kept after cancellation/failure. Totals above 100 MiB require confirmation.
The Cell Manager has size, status, progress, Renew and delete actions, like Protomaps. Its independent
**Select All Visible** selects rows without changing **Use**. **Renew Selected** refreshes NOAA cells;
renew local cells by reimporting updated originals. **Show on Map** fits selected cells and
**Delete Selected** requires confirmation. **Layers** directly on the map opens object checkboxes with S57 names/IDs.
**Base** reduces the chart to coast, safety contour and isolated dangers in safe water; the simulator's
safety contour defaults to 30 m or the next deeper contour. **Depth & Safety...** sets contour and safety depth independently; settings also travel in configuration and Map & Layers Start-up. **All Layers** restores all objects.
**Other Layers** is subdivided into traffic, ports, infrastructure, areas, natural features and chart information.
**All in this group** switches an entire branch including descendants; a partial check indicates mixed selections.
The complete legacy object dictionary can be filtered without truncating selections above 200 layers.
Landmarks, buildings, tanks/silos, mooring facilities, cranes, pylons and harbour points have compact symbols.
Popups identify their object class and available categories. Dense minor points are reduced to keep the chart readable;
navigation aids, hazards and conspicuous landmarks are not reduced. Unsupported point classes use a small neutral cross.
Class-level symbols also work on older imports; Renew adds available subtype attributes without guessing them.
An unfinished file/cell cannot partially replace its installed charts. NOAA download requires Internet;
local import/display works offline without an external GDAL installation.

The 2D map source selector offers **OpenSeaMap (Online)**, **Protomaps (Offline)** and **S-57 (Experimental)**,
disabling unavailable choices. Selecting local charts does not disconnect Live AIS: Offline mode is separate.
Source/layer choices, enabled cell IDs, Base and raster-package selection are saved in configuration and the independent
**Map & Layers** Start-up group, but chart bytes are not. Offline mode and water masks remain in **Offline**.
Transfer original exchange sets and import them on another computer; Protomaps area exports are independent.
With S57 selected, Avoid Land prepares local ENC water/land masks independently of displayed layers or zoom.
The finest available enabled cell determines the mask; coarser cells only fill uncovered gaps.
The bounded ENC cache and preparation workers are separate from the background map renderer.
Before creating Generated AIS, the simulator prepares the complete configured AIS radius around
own ship in the background. The AIS status shows progress; an initial large-area preparation can
take tens of seconds. Moving own ship or changing the radius prepares the new area and reuses
cached overlap. A bounded regional sampling mask helps spread targets across the area; exact
position and hull checks still use detailed local masks, even for a large radius or zoomed-out map.
Individual failed tiles are reported and retried later, without blocking all other usable water.
Unknown distant mask data does not initiate a turn or braking; movement itself still requires known water.
When adding a synthetic target, a short water-data preparation is retried automatically without a modal warning.
Generated positions use spacing and full bow/stern/beam checks, rather than accumulating around the first
prepared water tiles. Requested counts are approached incrementally where suitable water is available.
Large ENC catalogues are filtered by visible bounds and scale: overview cells first,
harbour detail as you zoom in. Detail-limit messages ask for a closer view; missing overview cells can leave
an empty chart until you zoom in. Overlapping scales do not stack: the most detailed enabled cell owns
its coverage; coarse charts fill remaining gaps. Old indexes use cell bounds; Renew/reimport saves exact
M_COVR footprints and additional symbol attributes. Existing imports remain usable without conversion.
S-57 stays offline but is marked Experimental. Buoys, beacons, cardinal/special marks, lights and hazards
have simplified S-52-oriented symbols using available attributes; soundings show metres. Traffic arrows, area markings, cable/pipeline patterns, light sectors and conditional hazards are also supported. Older imported cells need renewal to retain newly added attributes. Continental views use the schematic world map until zoomed closer. This is not the
complete Presentation Library or certified ECDIS; S63 is unsupported.
Depth-area colours use the selected safety contour and the available contour in each displayed cell.
A dredged area is not automatically depicted as safe. Overview cells can encode a broad depth range
where a harbour cell has a higher, more precise minimum depth, so depth colours can change on zoom.
This portrayal is separate from Avoid Land and does not provide under-keel-clearance protection.
**Renew Selected** processes available NOAA cells individually. Cells absent from the active catalogue
are skipped, failed downloads/imports do not prevent later cells from being processed, and installed
versions are retained on failure. The summary reports completed/failed/skipped counts; hover over a
failed or skipped row's status for details. Local or withdrawn cells require updated original files/folders.
See [S57 instructions and limitations](v2_offline.md#s57--enc-charts).

In **Settings > Offline**, use **Add Area...** under **Offline maps** while Internet is available. **Navigate** lets you pan and zoom between areas or polygon corners. Choose a detail level, then **Rectangle** to drag or **Polygon** to click 3 to 64 corners. Close a polygon with **Close Polygon**, Enter, double-click or the first corner. Crossed edges are rejected; only intersecting tiles are saved, with tile edges extending slightly outside the polygon. Mix up to 32 areas, each retaining its own detail. **Undo** removes the last unfinished corner or completed area; **Continue** queues new areas directly in the Offline table. Use **Download** or **Download Queued Areas**. One worker processes areas sequentially; each row shows tiles, actual size and progress with a stable Downloading status. Maps and water-source geometry are saved from Protomaps; needed masks are prepared locally and cached. Overlapping tiles are fetched once. Existing saved areas survive cancellation/failure.

When selecting **Add Area**, saved coverage is shown with filled, solid-border shapes; new selections use a dashed border and lighter fill. Cyan means Harbour detail, blue Regional detail, yellow Overview. Small labels show only the name, plus actual whole MB for saved areas with known sizes. Saved shapes are not queued again.

Saved areas are listed directly under **Offline maps**. Names save immediately. Changing detail or pressing **Renew** immediately starts a background update; polygon outlines are retained. Jobs with 500 or more unique tiles, or previously saved content above 100 MiB, require confirmation. Deleting saved areas requires confirmation and preserves all shared map/water data required by other areas. **Cancel Download** stops a running job. Renew/detail changes need Internet; rename/delete work offline. Existing maps remain available until an update finishes; failure/cancellation preserves them.

No MB size is guessed before download. New areas show `--` in the Size column; while downloading/after completion, measured saved content is shown. Map labels include MB only for saved areas with known sizes. These MB describe stored content, not downloaded bytes or connection speed. Archive requests are grouped and cached within bounded batches to reduce latency; the areas themselves remain sequential.

Very large areas above 20,000 tiles produce a warning instead of the previous rejection. The per-download memory safety ceiling is now 200,000 unique tiles (ten times the old limit). UI counting does not allocate all those tiles, so drawing and queuing remain responsive. Download/storage safety limits remain active.

Check multiple saved area names, or **Select All**, then **Export Selected...** to create a portable `.mbtiles` file. **Import Areas...** loads the exported areas without Internet. Polygon shapes, existing areas and shared tiles are preserved; exact duplicate shapes are skipped. Exports contain map/water data, not API credentials. Transfers run in the background and can be cancelled safely.

Use the same checkboxes with **Delete Selected** to remove multiple/all saved areas after one confirmation (default **No**). Unchecked areas, their shared tiles and queued downloads are retained. Selection actions are disabled while a job is running.

**Offline mode** stops Internet-backed maps, Live AIS and weather but leaves local simulation and UDP/TCP/Serial operating. Saved water masks support Avoid Land; disable it for unrestricted traffic when detailed water coverage is unavailable. Outside saved map coverage, bundled schematic coastlines are shown. Google 3D is online-only. The independent **Offline** Start-up group saves this selection. JSON exports store filenames, not map bytes; use the area export/import controls to transfer map data separately. See [Offline Operation](v2_offline.md) for storage paths, limits and USB transfer.

## Installation And Startup Diagnostics

Release packages bundle Python, PySide6, QtWebEngine, Qt plugins and application assets. Windows and Ubuntu users do not install Python, pip, Git, Bash or `winget` to run the program. Build scripts are for release operators only. Startup failures are recorded in `startup.log` inside the V2 user configuration folder and shown as a short graphical message rather than a Python traceback. Ubuntu launchers additionally check linked system libraries and provide Retry/Close guidance when an unavoidable OS library is missing. See `docs/v2_dependency_matrix.md` for the exact distinction between build and runtime requirements.

On Ubuntu, use the supplied `.deb` for a normal registered installation with application-menu entry and icon. AppImage and tar.gz remain portable launch options and do not require installation. Closing the interactive application always opens an opaque confirmation window; Save and Save As are added when the current configuration differs from the last saved state.

For KVM/autostart setups, a monitor recognized after startup triggers window-geometry recovery. Unreachable visible application windows are moved onto an available display; intentionally minimized or hidden windows and valid second-monitor positions are retained. A running graphical desktop session is required even when no physical monitor is initially connected. See [display recovery](DISPLAY_RECOVERY.md) for configuration, troubleshooting and limitations.
