# 3D Navigation View - 2.3.1

The optional 3D view displays Google scenery plus simulator-authoritative own
ship/AIS overlays. Google 3D Maps uses Maps JavaScript API; Photorealistic Tiles
uses CesiumJS and the Map Tiles service. Auto probes provider availability.
The API KEY action stays accessible with no configured key or during an error.
No key is supplied in the source snapshot.

Both renderers use the new HUD by default: a compact heading tape, Speed/Depth
on the left and Wind/Current on the right. SOG/STW and true/apparent wind can be
selected independently. Depth means below keel; true wind is FROM true,
apparent wind is relative to the bow, and current SET is toward true. Negative
selected speed and ASTERN are red. ASTERN and the secondary speed readout use
larger text; AHEAD retains its normal style. AIS names, three-minute vectors,
output-status colors and hulls remain separate map overlays, not an AIS HUD list.

FWD starts at elevation 9 degrees and 80 percent of the earlier vessel-dependent
range. 3D uses bearing -40 degrees, elevation 15 degrees and 1.4 times FWD
range; TOP is unchanged. CAM starts hidden. HUD/instruments are top-aligned;
API KEY and view controls sit at the bottom with attribution clearance. Drag to
rotate; right/Shift-drag or two-finger movement pans; wheel/pinch or both mouse
buttons with vertical movement changes range. Double-click restores FWD.
OS window controls/F11 handle the separate window's fullscreen state.

`src/maritime_simulator/ui/forward_view.py` owns Qt lifecycle, settings, native
gesture interception and coalesced JSON messages. Browser resources are in
`ui/web/forward_view/`. Google overlays use a screen canvas to avoid scenery
occlusion; the source is not a Google offline-tile downloader.

Optional Playwright checks in `scripts/verify_forward_view_web.mjs` cover
missing-key/error recovery, overlay pixels, gestures and bounded-memory fixture
updates. These do not validate a real key, live tile quality or a specific GPU.
Keep Google/Cesium attribution visible and observe upstream service terms.

The heading tape reuses 39 marks, depth history is bounded to 120 samples and
unchanged instrument canvases are not redrawn. The HUD has no independent
animation loop or simulation state. `scripts/verify_hud_beta_web.mjs` retains its
development filename and checks the final HUD across six viewport sizes,
including 72 SOG/STW speed-state cases; that name is not a beta release label.
