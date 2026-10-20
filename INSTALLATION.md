# Installation

Use the version-specific assets on the project's GitHub Releases page. Do not
download a build-source archive to install the application. Existing application
packages include Python and Qt; end users do not need Git, Python, Inno Setup or
the developer build scripts.

## Choose a package

| System | Installer | Portable alternative |
| --- | --- | --- |
| Windows | Setup EXE | ZIP, fully extracted before starting |
| Ubuntu | DEB | AppImage or tar.gz |
| Ubuntu 22.04 amd64 | Explicitly labelled 22.04 DEB | Labelled 22.04 AppImage or tar.gz |
| macOS Apple silicon | DMG | Application ZIP |

Only assets actually attached to a release are available. A newer Ubuntu build
is not interchangeable with the 22.04 compatibility build; the current macOS
downloads do not imply Intel-native support.

## Verify the download

Compare SHA-256 with the manifest for that exact release. This detects a changed
or incomplete file, not a trusted developer signature. Check the download source
as well. Substitute the actual filename, including version and compatibility label.

```powershell
Get-FileHash .\Maritime-Ship-Simulator-V2-2.3.0-windows-setup.exe -Algorithm SHA256
```

```bash
shasum -a 256 Maritime-Ship-Simulator-V2-2.3.0-macos.dmg
sha256sum Maritime-Ship-Simulator-V2-2.3.0-ubuntu-installer.deb
```

## Windows

Close existing instances, run the setup EXE or extract the portable ZIP to a
writable directory, then start the application and check About. The packages
are unsigned. Where policy permits, SmartScreen can offer More info > Run anyway
for a verified, trusted download. Do not disable Windows security globally.
Allow only the required traffic on the intended trusted network profile.

## Ubuntu

Install the DEB using APT so its declared dependencies can be resolved:

```bash
sudo apt install ./Maritime-Ship-Simulator-V2-2.3.0-ubuntu-installer.deb
```

For 22.04 use the explicitly labelled package. `GLIBC_2.38 not found` indicates
a platform mismatch; do not replace the system GLIBC manually. An AppImage can
need executable permission (`chmod +x downloaded-file.AppImage`). Extract tar.gz
packages completely and use the supplied launcher.

An offline installation may still need distribution libraries that are not
installed yet. Arrange those APT packages before disconnecting. Serial access
needs the appropriate logged-in user's device permissions; do not run the
simulator as root as a workaround.

## macOS

Copy the application from the DMG into Applications, or extract the application
ZIP completely. The original packages have no Developer ID signature/notarization.
If macOS blocks the verified application, attempt to open it and then use System
Settings > Privacy & Security > Open Anyway where offered. Confirm only this
application; never disable Gatekeeper globally. See
[Apple's instructions](https://support.apple.com/en-us/102445). Grant local-network
access when needed for configured network inputs/outputs.

## Existing settings and first start

User settings/maps are stored separately from the application. Check Start-up,
mode, output endpoints and installed map sources before connecting equipment.
For an isolated receiver test, start in Sensor mode with one UDP output to
127.0.0.1 and no provider credentials. Output Monitor Freeze pauses only its
display, not transmission.

For KVM/autostart use, wait for the graphical desktop session. Window geometry
recovery helps when a display appears later but cannot repair a graphics-driver
failure or create a missing desktop session. Test the actual switching sequence.

## Licensing

Original project code uses MPL 2.0. Third-party libraries and chart data have
separate terms. See [release licensing](docs/RELEASE_LICENSING.md). Install only
the version-matched packages listed on the release page; an updated guide does
not replace native build or platform validation.
