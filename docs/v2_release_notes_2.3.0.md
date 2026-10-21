# Maritime Ship Simulator V2 2.3.0

Prepared 21 October 2026. Unsigned public source baseline under MPL 2.0.
Native applications still need rebuilding and platform qualification; no assets
or source have been published by this preparation step.

## Controls

- Tunnel bow thruster with motor run-up/reversal and STW-dependent effectiveness.
- Vessel-specific power in kW/MW, rated RPM and adjustable installation parameters.
- One horizontal Bow Thruster instrument with actual output and yellow order marker.
- External Input groups for Rudder, Bow Thruster and Engine Telegraph.
- Main and optional Fallback source selection, followed by Manual, Freeze or Zero
  when neither external input is current. Fresh main input regains priority.
- Independent Rudder XDR Order/Response inputs. A/D carries degrees; A/P and
  legacy G/P carry signed percent of the vessel's maximum rudder angle.
- Propulsion XDR supports A/P by default and legacy G/P; values are -100 to +100%.
- Valid Type/Unit dropdowns, exact editable IDs, coherent dual-rudder sentences
  and invalid-value rejection. TRC RPM demand and PRC lever demand remain supported.
- Visible input status and responding, locked manual controls while externally owned.
- Full configuration and Start-up persistence of source, loss, endpoint and filter settings.
- Compact source settings with subdued, standard-style radio buttons.
- Physical mouse-wheel zoom is distinguished from trackpad panning before the
  browser's gesture heuristics. Both 3D providers support holding both mouse
  buttons and dragging vertically to zoom.
- The 2D map also supports both-button vertical zoom, including right-first
  presses that initially open the context menu. Its native scroll routing keeps
  mouse-wheel zoom separate from trackpad pan throughout phased scroll sequences.
- macOS wheel input is no longer misclassified solely because Qt shares a
  TouchPad descriptor with the mouse. Fractional trackpad movement is preserved
  and pan rendering is coalesced; the author confirmed the input correction.
- Double-click sliders to reset to neutral or their configured default; engine
  telegraph resets to Stop, not the first detent. External controls stay locked.
- Slider reset is local to the widget, avoiding an application-wide Python
  event filter that can crash PySide while QtWebEngine constructs native objects.
- In-page map-source and offline-detail menus avoid oversized native browser
  popups at fractional Windows display scaling without disabling DPI scaling.

## Public distribution

One English User Guide incorporates the former RC addendum. No equipment-specific
integration guide belongs in the public download set. Release scripts default to
unsigned production. No provider credentials or user charts are included.

Original project code and documentation use MPL 2.0; upstream components and
map data retain their own licenses. Unused QML imports and optional Qt families
are excluded, and bundled output is checked for those components. Exact native
dependency notices and target-platform tests remain publication gates.
