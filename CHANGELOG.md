# Releases

## 2.3.0

Initial public baseline under MPL 2.0. Tunnel bow-thruster dynamics, independent
external rudder/engine/thruster inputs, Main/Fallback sources and configurable
Manual/Freeze/Zero loss actions. Rudder XDR supports degrees/percent; propulsion
XDR supports A/P and legacy G/P. Input panels fit visible content and source
options collapse to a summary. Ship Control aligns rudder and bow controls below
the telegraph, with Keyboard control at the bottom.

Offline Protomaps, experimental S57/NOAA management, synthetic encounters,
fault injection and online 3D views are included. One English User Guide covers
operation. Equipment-specific integration documents and old history are excluded.
Native builds and bundled dependency notices need platform qualification before
publication. See the release notes and validation record.

The final Windows build includes physical mouse-wheel versus trackpad-source
tracking, a shared pan/zoom source lease for 2D/3D views, and stable route-row
selection during pinch gestures. The author confirmed the Windows 11/Dell
trackpad correction. Prepared Linux binaries target Ubuntu 26.04.1 LTS amd64;
macOS and Ubuntu keep the previously checked baseline, with its exact source
provided separately. See [build provenance](docs/NATIVE_BUILD_PROVENANCE.md).
