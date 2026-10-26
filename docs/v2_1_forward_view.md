# 3D Navigation View - 2.3.1

The optional 3D view displays Google scenery plus simulator-authoritative own
ship/AIS overlays. Google 3D Maps uses Maps JavaScript API; Photorealistic Tiles
uses CesiumJS and the Map Tiles service. Auto probes provider availability.
The API KEY action stays accessible with no configured key or during an error.
No key is supplied in the source snapshot.

Both renderers provide FWD/3D/TOP presets, heading tape, own-ship readouts, AIS
names, three-minute vectors, output-status colors and camera sliders. Drag to
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
