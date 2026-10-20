# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from PySide6.QtWidgets import QDialogButtonBox, QTextBrowser, QVBoxLayout

from .dialogs import OpaqueDialog
from ui.style import NUMERIC_FONT_FAMILY


class UserGuideDialog(OpaqueDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Maritime Ship Simulator V2 - User Guide")
        self.resize(820, 720)
        layout = QVBoxLayout(self)
        guide = QTextBrowser()
        guide.setOpenExternalLinks(True)
        guide.document().setDefaultStyleSheet(
            f'code {{ font-family: "{NUMERIC_FONT_FAMILY}"; }}'
        )
        guide.setHtml(
            """
            <h1>Maritime Ship Simulator V2</h1>
            <h2>Bow thruster and external control</h2>
            <p>The horizontal <b>Bow Thruster</b> slider below the telegraph orders port (-) or starboard (+)
            RPM. Double-click for zero. The Bow Thruster instrument uses one horizontal actual-RPM
            bar and a yellow order triangle, showing motor
            ramp-up and reversal; effectiveness decreases smoothly with speed through water, not SOG.
            In Vessel Models, configure power in kW/MW (0 = not fitted), rated RPM, ramp time,
            speed-loss curve, thrust and installation parameters. Factory values are representative
            simulator assumptions, not calibrated ship-trial data.</p>
            <p>Double-click a slider to reset it: rudder to midships, bow thrust to zero,
            engine telegraph to Stop. Controls without a valid zero return to their configured
            default within the current limits. Disabled external controls remain locked.
            Camera sliders reset bearing, elevation or range independently.</p>
            <p><b>Settings - External Input</b> groups Rudder, Bow Thruster and Engine Telegraph.
            Choose Main Control Source, optional Fallback Control Source, then On Input Loss.
            Main can be Manual; external main and fallback must differ. Each protocol has UDP/TCP/Serial
            settings. TRC uses thruster 1 RPM demand (P = percent, R = RPM); PRC uses engine 1
            lever demand with valid status A. XDR matches Type/Unit/ID exactly: A/P by default, with
            G/P available for legacy inputs; IDs are BowThrust#0 or EngTele#0. Type and Unit use
            restricted dropdowns. Commands are -100 to +100%; positive means starboard/ahead.
            External commands visibly move disabled controls. After 2.5 seconds without valid main input,
            fallback takes over. If neither is current, Manual unlocks control (rudder/bow start at zero,
            engine holds its last order); Freeze holds the last command or measured response; Zero orders
            zero with normal motor/steering response. Freeze and Zero keep manual edits locked.
            Fresh valid input resumes external control unless Manual is selected. Priority returns on
            the first valid preferred sentence. Engine/bow input works in model modes; external rudder
            is used in External mode, with Manual available. Track retains route steering and route-end
            Stop; external engine selection overrides planned speeds. Sensor mode remains manual.
            Input settings and vessel models are included in configuration and Start-up groups.</p>
            <p>TRC/XDR and PRC/XDR input indications follow the rudder lamps: green for a valid value
            within 5 seconds, grey for a matching empty field, yellow after 5-30 seconds, red after
            30 seconds or before first reception. The lamps also monitor inputs in Manual. Their
            age thresholds are separate from the 2.5-second control timeout.</p>
            <p>The upper status strip shows simulation scale, own-ship position and the current projected sensor
            values. Values are enlarged for quick operator scanning. Use the mode bar to open Instruments, Output,
            Advanced Protocols, Settings, this Guide and About.</p>
            <h2>Offline maps</h2>
            <p>The Offline tab separates <b>Protomaps</b> saved map areas from <b>S57 / ENC</b> charts.
            The 2D map source selector offers <b>OpenSeaMap (Online)</b>, <b>Protomaps (Offline)</b>
            and <b>S-57 (Experimental)</b>; missing sources are disabled. S-57 still works offline.
            A local map can be used with Live AIS
            still connected. Offline mode separately stops Internet use.</p>
            <p><b>Settings - Offline</b> pauses Internet-backed maps, Live AIS and live weather while
            local simulation, UDP/TCP/Serial, routes and instruments continue. The built-in schematic
            coastline map works without downloads but omits harbour detail. While online, select
            <b>Add Area</b> under <b>Offline maps</b>. Downloaded coverage is filled with solid borders;
            new selections are dashed. Cyan means Harbour detail, blue Regional and yellow Overview.
            Small labels show the name, plus actual whole MB for saved areas with known sizes;
            new areas have no size forecast and only new selections enter the download queue.
            In <b>Navigate</b> you can move and zoom the map;
            in <b>Rectangle</b> you can drag an area; in <b>Polygon</b> click 3 to 64 corners.
            Close with <b>Close Polygon</b>, Enter, double-click or the first corner. Crossed edges are invalid.
            Only tiles intersecting the polygon are downloaded; tile edges can extend slightly outside it.
            Each area retains its Harbour, Regional or Overview detail. Navigate between corners or areas
            if needed. Undo removes the last unfinished corner, otherwise the last completed area;
            Continue queues new areas directly in the Offline table, where names and detail can be adjusted.
            Each row shows tiles, size, status and progress. Download or Download Queued Areas
            saves maps and local water-source data without an API key. Needed water-mask tiles are prepared
            in a bounded background worker during AIS use and cached, including offline; the first query
            can briefly wait for preparation. The Size column is empty until actual content is saved, and
            areas above 20,000 tiles warn instead of being rejected. The memory safety ceiling is
            200,000 unique tiles per download, ten times the old limit. Overlapping tiles are fetched only
            once and existing areas survive a cancelled or failed download. Enable Offline mode to use
            saved maps; outside coverage the schematic coastline is shown. Avoid Land uses saved water
            data. Without detailed local water data, disable Avoid Land for unrestricted Generated/Synthetic
            traffic. Capture the Offline Start-up group for unattended offline demonstrations. JSON exports
            store package filenames, not map bytes. Google 3D requires Internet and is not saved offline.</p>
            <p>Saved areas are listed directly under <b>Offline maps</b> in Settings. Rename/delete areas,
            change their detail or press <b>Renew</b> to refresh the basemap. Names save immediately;
            Renew/detail changes start directly. Jobs with at least 500 unique tiles or previously saved
            content over 100 MiB, and all saved-area deletions, require confirmation.
            One worker processes areas sequentially and reuses shared tiles. <b>Cancel Download</b> stops a running job.
            No MB size is predicted before download. Displayed MB describe actual stored content,
            not network throughput. Bounded groups of archive requests
            and cached subranges reduce repeated small downloads without increasing area concurrency.
            Downloading covers reading, rendering and saving; its status no longer alternates with Reading.
            Polygon shapes are retained by rename, Renew, detail changes and export/import.
            Renew/detail changes require Internet; rename/delete work offline.
            Existing maps remain available until the background update succeeds. Shared tiles are kept
            when deleting an area; removing the last area restores the schematic fallback.</p>
            <p>Check saved area names or <b>Select All</b>, then <b>Delete Selected</b> to remove
            multiple/all saved areas with one confirmation. No is the default. Unchecked areas,
            shared data they require and queued downloads remain intact.</p>
            <p>Check one or more saved area names, or <b>Select All</b>, then <b>Export Selected</b> to save
            a portable MBTiles file. On another computer, <b>Import Areas</b> adds all areas in that file
            without Internet. Existing areas and shared tiles remain unchanged; duplicate shapes are skipped.
            Exports contain map/water data, not API credentials. Transfers run in the background and can be
            cancelled safely; incomplete packages are rejected without replacing saved maps.</p>
            <h3>S57 / ENC charts</h3>
            <p>In <b>Settings - Offline - S57 / ENC</b>, <b>Import S57</b> accepts unencrypted .000 cells
            or ZIP exchange sets. The Import menu also offers <b>Import Folder</b>, scanning subfolders.
            Keep numbered updates beside the base cell or inside the ZIP. SevenCs compiled .7CB/.7CR
            charts require the original unencrypted .000 files instead.
            A complete NOAA catalogue can be imported in one operation, up to 20,000 cells/250,000 files.
            Cells are staged individually, with a 512 MiB limit per cell including updates, not per catalogue.
            If a folder contains unpacked cells and a redundant backup ZIP, unpacked cells take precedence;
            select the ZIP explicitly to use that edition. Completed cells remain installed after cancellation.</p>
            <p>
            <b>Download NOAA</b> lets you search the official catalogue by cell ID/area, select cells and
            download/import them in the background. <b>Select All Visible</b> affects only the filtered rows,
            retaining hidden selections. The entire visible catalogue can be selected. Only missing or
            outdated cells are downloaded. Up to five downloads overlap one importer, with at most ten
            cells staged ahead; totals above 100 MiB require confirmation. Cancel retains completed cells.
            Refresh NOAA Catalog checks revisions in the background without downloading charts.
            The online manager also checks once when first opened. Download needs Internet; local import works offline.</p>
            <p><b>Use</b> enables each cell; select rows and <b>Show on Map</b> to fit their coverage.
            The manager shows size, status and progress, with row Renew/delete and Renew Selected/Delete Selected.
            Its Select All Visible selects action rows, not Use settings. Renew Selected checks revisions
            and skips current NOAA cells. Highlighted <b>Renew</b> means an update is available;
            highlighted <b>Download</b> means the cell is missing. Unhighlighted <b>Reload</b>
            explicitly downloads a current revision again, never downgrading newer local charts.
            Only required downloads above 100 MiB ask for confirmation. Unavailable cells cannot be
            downloaded from NOAA; local charts offer Import instead.
            Local charts need updated
            original files/folders. Scroll the list during work; chart changes are temporarily disabled.
            Deletion requires confirmation.
            <b>Layers</b> directly on the map opens grouped checkboxes
            for depth, land/coast, aids, hazards and other objects, with names and S57 IDs.
            Other Layers contains subgroups for traffic, ports, infrastructure, areas, natural features and
            chart information. <b>All in this group</b> switches the complete branch; a partial check shows
            mixed selections. <b>All Layers</b> restores all objects.
            <b>Base</b> displays coast, safety contour and isolated dangers in safe water, not an empty map.
            <b>Depth &amp; Safety...</b> sets safety contour and safety depth independently in metres.
            Both default to 30 m; the next deeper chart contour is used if necessary. Shallow soundings
            and hazards are emphasized without generating an automatic grounding alarm.
            Source/layer choices, enabled cell IDs and Base are saved in configurations and the separate <b>Map &amp; Layers</b>
            Start-up group, independently of Offline mode. Depth/safety settings are included.
            Chart data itself is not embedded: transfer the original exchange sets and import on another
            computer. Protomaps area exports are separate from S57 chart data.</p>
            <p>With S57 selected, <b>Avoid Land</b> uses the finest available enabled ENC cell,
            independently of zoom and displayed layers; coarser cells fill coverage gaps only.
            Mask preparation and caching do not share the map renderer's work queue.
            Generated AIS first prepares the configured radius around own ship in the background;
            the AIS status shows progress. Large areas can take tens of seconds initially.
            Position/radius changes reuse cached overlap and prepare the new area.
            Regional sampling is memory-bounded, while exact position and hull checks keep detailed
            local masks. A failed tile does not block the other usable water areas.
            Unknown distant data does not trigger avoidance; actual movement still requires known water.
            Synthetic target creation waits briefly for local mask preparation without a modal warning.
            Depth colours depend on the selected safety contour and each chart's depth ranges.
            A dredged area is not automatically safe; overview and harbour cells can give different
            minimum depths. This is separate from the water/land mask and is not under-keel protection.</p>
            <p><b>Renew Selected</b> continues after individual NOAA download/import failures.
            Cells missing from the active catalogue are skipped and installed charts are kept.
            The completion summary separates successful, failed and skipped cells; hover over a row's
            status for details. Renew local/withdrawn cells from updated original files/folders.</p>
            <p>S-57 is Experimental: simplified S-52-oriented simulator portrayal, not certified ECDIS
            or a complete IHO Presentation Library. Buoys use available shape/colour attributes, cardinal
            and special marks use simplified topmarks; lights, wrecks, rocks and obstructions have distinct
            symbols. Traffic directions, anchorage/restricted/fishing/military markings, cable/pipeline
            patterns, light-sector diagrams and known clearances are also shown. Sector arcs indicate
            direction, not geographic visibility range. Continental zoom levels use the schematic world
            overview until zoomed closer. Landmarks, buildings, tanks, mooring facilities, cranes, pylons
            and harbour facilities have compact symbols. Popups show the class and known categories.
            Dense minor land/coastal points are reduced; navigation aids, hazards, conspicuous landmarks
            and lighted moorings are not reduced. Unsupported point classes use a small neutral cross.
            Full legacy dictionaries with over 200 layers can be filtered and saved without truncation.
            Soundings show metres. Previously imported cells retain their existing attributes;
            Renew or reimport obtains additional portrayal attributes and exact coverage footprints.
            Encrypted S63 charts are not supported. Only cells covering the view at a suitable scale are loaded:
            overview cells first, detailed harbour cells when zoomed in. Runtime detail is bounded;
            the most detailed available cell owns overlapping coverage; coarse cells fill gaps.
            Older indexes use cell bounds until renewed.
            zoom in if detail is limited or no cell is available at the current scale.
            Check cell issue dates and source licences. No separate GDAL installation is needed for packaged apps.</p>
            <h2>Control modes</h2>
            <p><b>Sensor Control</b> publishes manual sensor values. Drag the HDG and COG dials; use
            <i>COG follows HDG</i> to retain their offset and <i>ROT drives HDG</i> to integrate heading.</p>
            <p><b>Ship Control</b> runs the selected vessel model. The telegraph detents can be clicked or
            dragged continuously between detents. The active detent is bold; between detents both surrounding
            stages are bold. Enable keyboard control to use Up/Down for detent steps and Left/Right for rudder.</p>
            <p><b>Track Control</b> lets the Track Controller own rudder order. Manual telegraph speed remains
            available unless <i>Use planned speeds</i> is enabled in Route Planning. Operating Turn Rate or Turn
            Radius makes that value fixed; the other value is calculated from current speed and shown dimmed,
            but remains directly operable. Controller phases are shown as operator labels such as
            <i>Inbound Straight</i> and <i>Planned Turn</i>.</p>
            <p><b>External Rudder</b> accepts ROR or XDR Order before the steering gear, and RSA or
            XDR Response as measured rudder response. Rudder XDR defaults to A/D for degrees; A/P and legacy
            G/P map -100 to +100% to the vessel's maximum rudder angle. IDs default to RudderOrder#0 and
            Rudder#0. An optional PORT ID requires both values in the same valid sentence. Main/fallback and
            Manual/Freeze/Zero apply as described above. PORT and STBD always come from one complete source.
            UDP, TCP or Serial listeners start automatically. Input indications are green through five seconds,
            yellow above five through 30 seconds, and red above 30 seconds or before first reception. View Port
            shows the exact received lines. A grey lamp means a checksum-valid sentence arrived but the field for
            that specific PORT/STBD channel was empty. A valid channel remains usable even when its paired field is
            empty, for example <code>$AGRSA,,V,-0.0,A*52</code>.</p>
            <h2>Route Planning</h2>
            <p>Double-click editable table cells. Add, remove, reverse or import waypoints. Copy and paste cell
            ranges with the standard shortcuts. Turn Rate and Turn Radius have separate columns: entering one
            selects that fixed mode and the other column becomes Calculated. <i>Use planned Turn Rate / Radius</i>
            selects waypoint values; clear it to use the Track Control values throughout the route. Planned cells
            always contain numeric values (defaults: 10 kn and 10°/min); there is no separate Manual table value.
            Right-click
            Speed or Turn values to apply one value to every waypoint. Wheel-over marks and planned arcs on the
            map are generated by the same geometry used by Track Control; red arcs and table values indicate
            overlap. Adjacent conflicts use an averaged transition marker and a dashed compromise path, while
            requested speed, radius and ROT remain unchanged. The controller follows that smooth middle path
            through the conflict instead of switching directly between two impossible overlapping arcs. Its path
            progress only moves forward, and normal pre-WOP limiting is not applied again after transition guidance
            has calculated the command. Vessel steering and ROT limits still apply, so physically impossible
            geometry remains highlighted and is followed as closely as the model permits. The status
            shows elapsed and total distance for the active leg. Speed, ROT and radius cells use one decimal. Route
            Planning and the left control area can be collapsed while their essential controls remain available.
            The compact control area uses narrow vertical sliders and identifies whether ROT or Radius is active.
            The route-position slider seeks own ship along the active route.</p>
            <h2>Environment, AIS and sensors</h2>
            <p>Simulation time is selected in the upper status row; accelerated values are highlighted red.
            Environment contains compass dials for wind
            and current plus speed and depth controls. Atmospheric data owns the standard XDR values for air
            temperature in Celsius, relative humidity, dew point and surface pressure. These can use editable manual
            values or no-key Open-Meteo values for the current own-ship position. Live updates are asynchronous and
            refresh after a significant position jump; failures retain the previous XDR values without blocking
            simulation or output. MDA and MTW remain separate manual sensors. The selected source is stored in
            complete configurations and the Environment Start-up group. Sensor Noise provides independent deterministic noise for
            the projected sensor channels; it never feeds noise back into vessel dynamics. Wind direction and
            speed noise is shared by MWD, MWV relative and MWV true-theoretical output. Every channel has a
            0.1 to 60.0 second Cycle time slider; amplitude and cycle time are saved in complete configurations
            and in the Noise Start-up group. The rightmost Other tab
            contains dual rudder, transversal speed, meteorological and propulsion values. AIS has its own tab
            for generated/live data and display/output limits. Its All / SOG dependent output filter can exclude
            targets at or below the selected minimum SOG; the prepared default is 0.5 kn. The threshold is disabled
            in All mode. Both values are stored in complete configurations and the AIS Start-up group. Live AIS sources are aisstream.io and Norwegian
            Coastal Administration data through BarentsWatch. aisstream.io needs an API key; its range and
            worldwide options define the provider subscription. BarentsWatch needs an AIS client ID and secret;
            its range is a local own-ship filter and Show all Norwegian coverage disables only that filter, not
            the provider's Norwegian geographic limit. Credentials can be entered in Live AIS Settings or
            supplied by environment variables; no provider credentials are bundled or printed in diagnostics. Provider, transport,
            subscription, frame and valid-target status remain separate. Synthetic Traffic Generator supports
            multiple editable targets and CPA/TCPA encounter presets. Right-click the 2D map to add a target on
            detected water; drag the target or its vector to change position, course, heading and speed. Repeated
            additions receive naturally separated defaults. <b>Avoid Land</b> applies to Generated and Synthetic
            AIS and defaults to enabled. Disable it for offline use: creation, encounters, dragging and motion
            make no water-map requests, but targets may cross land. The setting is saved with the AIS configuration
            and Start-up group; it does not affect Live AIS. When enabled, generated traffic is accepted only with forward water
            clearance, evaluates port and starboard alternatives early, favours small stable corrections and varies
            its available turn rate up to 60 degrees per minute. It slows progressively only when the safe corridor
            becomes limited and no longer falls back to repeated 180-degree turns.</p>
            <p>The Fault Injection tab follows Other. It changes projected sensor/instrument values or protocol
            transmission only; authoritative vessel dynamics remain untouched. Saved scenario selections always
            load inactive and require an explicit operator action after every start or configuration load.</p>
            <p>The Noise tab configures projection-only disturbance for every supported sensor, including XTE.
            Cycle time is the duration of one complete deterministic noise oscillation.
            Noise never changes vessel dynamics. The Other tab contains dual rudder, transversal motion,
            manual MDA/MTW and propulsion/thruster channels. Output enable states are configured only in Settings.</p>
            <h2>Open source and support</h2>
            <p>Original project code is available under the Mozilla Public License 2.0.
            Third-party software and map data retain their own licenses.
            Continued development can be supported by purchasing
            <a href="https://mybook.to/MaritimeKnowledgeBites">Maritime Knowledge Bites</a>.</p>
            <h2>Output</h2>
            <p>Advanced XDR rows provide grouped IEC 61162-1:2024 recommended identifiers with only valid units.
            Air/water temperature, dew point, relative humidity, surface pressure, rudder, heel, pitch and roll
            presets can use live AUTO sensor values. Temperature presets use Celsius. In an ID such as Air#X, X is
            the editable data-instance number from 0 to 9999; presets start at #0.</p>
            <p>Settings starts with one UDP, TCP and Serial output path. The + menu adds further independent paths
            of any transport and x removes the selected additional path. Added paths start disabled and are stored
            with the complete configuration. Every protocol has an independent On state, rate and endpoint on each
            path, so the same data can run over several UDP, TCP and Serial ports at different rates. The dedicated,
            default-off XDR Environment section has separately selectable and timed rows for air temperature,
            relative humidity, dew point and surface pressure. Their distinct Air#0, Air#1, Air#2 and Baro#0 IDs
            are shown in the list. A sensor-row
            edit affects every child protocol only within the selected output path. TCP Client connects
            to a remote server. TCP Server listens on the configured local bind address and distributes output to all
            connected clients without allowing one slow client to block another. TCP Client retries refused, reset,
            timed-out and half-open connections using bounded writes; TCP Server remains listening after disconnect,
            while the remote client must initiate the replacement connection. The Serial tab lists ports reported
            by the operating system and remains editable for custom device paths. AIS VDM sends visible traffic
            targets; AIS VDO sends the simulated own ship as a Class A position report and starts disabled. Advanced
            VBW output includes stern transverse water and ground speed/status pairs. Each configured MWV cycle
            sends one relative R sentence and one true theoretical T sentence; the T angle is relative to the bow
            and uses true wind speed. Advanced
            can send validated IEC
            sentences once or cyclically. Output is a monitor only: scheduled output runs independently and the
            window shows what was actually sent on currently configured ports. Settings can save or load a complete JSON
            configuration including route, vessel-model overrides, output, input, AIS, Advanced and instrument layout.
            The <b>Start-up</b> tab defines a portable unattended Startup Setup. Its comparison table shows Current,
            Last Saved and Fixed values for every group; the selected startup source is highlighted. Tooltips show
            essential values and a short summary of changes, without exposing credentials. Last Saved changes
            only with Close and Save or an explicit Save Current action. Fixed changes only when it is captured.
            Position &amp; Heading has its own group, while Route captures the route currently loaded through Route
            Planning. Complete Last Saved or Fixed snapshots can be captured without silently changing the selected
            sources. Only Advanced rows whose captured cycle state is active restart automatically.
            The Vessel Models tab can tune or rename any vessel, copy a vessel into a new custom profile, delete
            custom vessels and reset each model to its shipped basis. Built-in selectors use the fixed order Crew
            Transfer Vessel, Container Feeder, 15k TEU Container, 24k TEU Container and VLCC; custom vessels follow.
            Built-in vessels cannot be deleted. Calculate
            Values creates a conservative controller baseline by interpolating the five tuned built-in vessels; it
            is a starting point rather than a replacement for vessel-specific test runs.</p>
            <h3>Vessel model parameters</h3>
            <p><b>Dimensions &amp; Speed</b> defines length, beam, maximum ahead/astern speed and acceleration/deceleration.
            <b>Dynamics</b> defines maximum rudder, hard-over rate, maximum ROT, ROT time constant, speed exponent,
            sway response and wind/current susceptibility. <b>Rudder effectiveness</b> changes the partial-rudder
            response curve: 1.0 is the shipped linear behaviour, values above 1.0 produce more turning effect at
            partial rudder and values below 1.0 reduce it. Full-rudder authority and the configured maximum ROT are
            unchanged. <b>Track Controller</b> contains the tuned straight-leg, recovery, turn, counter-rudder and
            WOP parameters. Calculate Values provides only a conservative starting point; verify custom ships on a
            representative route before operational testing.</p>
            <h3>Network addresses</h3>
            <p><b>Bind address</b> selects the local address on which an input listener accepts packets; use
            <code>0.0.0.0</code> to listen on all local IPv4 interfaces. <b>Multicast group</b> is the destination
            group transmitted by the source, typically in 224.0.0.0/4. <b>Interface IP</b> selects the local network
            adapter used to join that multicast group and is not the sender address. UDP output Auto mode infers
            unicast, broadcast or multicast from the destination address; multicast TTL controls how many routed
            network hops the packet may cross. TTL 1 normally confines multicast to the local network and does not
            affect output rate or unicast/broadcast traffic. For TCP output, Client mode uses the field as the remote destination;
            Server mode uses it as a local bind address, where <code>0.0.0.0</code> accepts clients on every local
            IPv4 adapter, <code>127.0.0.1</code> accepts only local clients, and a specific local address listens only
            on that adapter. Remote clients connect to the simulator computer's reachable address, never to
            <code>0.0.0.0</code>.</p>
            <h2>Map</h2>
            <p>Right-click for waypoint and own-ship actions. Drag waypoints to edit the route. The expand control
            moves the same live map into a separate window and restores it without creating a second WebEngine.
            Two-finger scrolling pans. Mouse wheel, pinch or Ctrl+wheel zooms smoothly around the map centre;
            holding both mouse buttons and moving vertically also zooms (up in, down out).
            Follow Own Ship instead keeps own ship as the fixed zoom point. The browser page itself remains at
            100 percent zoom, including when the gesture begins over a map control.
            In the detached map, the expand action or F11 toggles full screen; Escape returns to windowed mode.</p>
            <h2>3D Navigation View</h2>
            <p>Use Map / 3D above the main navigation area to switch in place. 3D Window moves the same renderer
            into a separate scalable Google 3D scene, without starting a second tile stream.
            Use the operating-system window controls to maximize or restore the detached view; F11 and Escape remain
            keyboard alternatives. The in-view FULL overlay is intentionally omitted, making the view suitable for
            a dedicated second monitor.
            It cannot steer or modify the simulation. Own ship and AIS targets are clear, visually extruded
            simulator overlays sized from their physical length and beam. Own ship is cyan; output-active AIS hulls
            are green and excluded AIS hulls are grey. Heading, a three-minute prediction and nearby-target
            details remain visible independently of Google's static scenery. Drag to orbit through 360 degrees;
            right-drag, Shift-drag or use a two-finger trackpad scroll to pan without zooming.
            Mouse wheel, pinch or holding both mouse buttons and dragging vertically zooms.
            Browser page zoom is locked to 100 percent so gestures over HUD text still control only the map. Double-click or FWD restores the defined
            forward view. 3D and TOP provide oblique and near-vertical presets. Bearing, elevation and range
            sliders provide bearing, elevation and zoom without a multitouch device. Simulator symbols are drawn
            in an always-visible overlay above Google geometry, and a network-free sky layer adds subtle static clouds
            without forcing continuous rendering. NAMES toggles AIS labels while leaving symbols and the HUD list visible.
            Loading appears only briefly during initialization or a large relocation.
            The opaque WebGL surface and bounded 30 FPS visual updates reduce Windows compositor flicker and leave
            protocol scheduling on its independent worker.
            A valid Google API key and network connection are required. Use the small API KEY action
            in the upper-left corner to store the key and choose Auto, Google 3D Maps or Photorealistic Tiles / Cesium.
            Auto selects the direct renderer where available and the Maps JavaScript 3D renderer after a confirmed
            regional restriction. Enable Maps JavaScript API for Google 3D Maps and Map Tiles API for Cesium; the
            dialog links to both official setup pages. The key is never bundled or shown in diagnostics. A complete configuration
            export intentionally contains the effective Google key and AIS credentials so a demo can be restored
            elsewhere; protect that JSON file as sensitive.</p>
            <h2>Performance Monitor</h2>
            <p>Open About and choose Performance Monitor. Enable collection to see UI response, simulation and
            Track calculation time, protocol rate, recent network errors, AIS target count and current app
            memory. Health uses a rolling 10-second window, so a historical slowdown does not keep the panel red.
            UDP/TCP sentence counts are informational; output health measures worker processing load instead.
            Serial bandwidth is assessed separately from measured sentence bytes, framing and baud rate.
            AIS counts and memory are grey informational rows, not arbitrary capacity warnings. Current RAM
            includes WebEngine child processes; shared pages may be counted more than once. If current RAM
            cannot be measured, the main-process peak is shown instead. Available system RAM, swap activity
            where supported and local map-cache budgets are sampled in the background every five seconds.
            Under memory pressure, unused maps can unload after one minute; reopening restores the camera,
            zoom and display choices. The active view is not automatically switched to another provider or 2D.
            Each marker is
            refreshed live at a low operator-friendly rate. Technical
            JSON remains available under
            <i>Show technical details</i>.</p>
            <p>Monitoring remains enabled until unchecked; closing the window only pauses its display refresh.
            In Settings - External Input, Rudder has separate ROR, RSA, XDR Order and XDR Response tabs, each with UDP, TCP and
            Serial transport tabs. Selecting a transport configures that listener; merely viewing the other
            endpoint does not restart a listener. All inputs remain monitored independently of the selected
            control source. The top-bar XTE readout is shown only in Track Control.</p>
            <p>The Rate of Turn instrument keeps the numeric IEC value unchanged while its seven-dot ring moves
            at four times the previous visual speed, making low rates easier to recognize.</p>
            <h2>Installation and diagnostics</h2>
            <p>Release packages include Python, PySide6, Qt and application resources. End users do not need
            Python, pip, Git, Bash or winget. Startup failures are written to a rotating diagnostic log in the
            V2 user configuration folder. Ubuntu launchers check unavoidable system-library dependencies before
            starting and provide Retry/Close guidance instead of an unexplained loader error. Use the Ubuntu
            <code>.deb</code> installer for a registered application-menu entry and icon; AppImage and tar.gz are
            portable alternatives.</p>
            <h2>Saving and closing</h2>
            <p>Settings can export or load a complete JSON configuration. Complete exports contain configured AIS
            credentials and the Google Map Tiles API key and must be transferred securely. Interactive closing always asks for
            confirmation. When values changed, choose Save, Save As, Close without saving or Cancel; a clean
            configuration offers Close or Cancel. Last Saved startup values are updated only by Close and Save;
            loading or exporting another configuration does not silently replace them. Close without saving restores
            the startup baseline without copying Fixed-overlaid values into Last Saved. Fixed/Last Saved policy is
            configured per group under Settings &gt; Start-up. Runtime configuration files are stored outside the installation,
            so portable builds do not depend on their original folder.</p>
            """
        )
        layout.addWidget(guide, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
