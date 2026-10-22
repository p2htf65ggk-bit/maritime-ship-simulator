# Installation

Use the version-specific assets on the project's GitHub Releases page. Do not
download a build-source archive to install the application. Existing application
packages include Python and Qt; end users do not need Git, Python, Inno Setup or
the developer build scripts.

## Choose a package

| System | Installer | Portable alternative |
| --- | --- | --- |
| Windows x64 | Setup EXE | ZIP, fully extracted before starting |
| Ubuntu 26.04.1 LTS amd64 | DEB | AppImage or tar.gz |
| macOS Apple silicon | DMG | Application ZIP |

Only assets actually attached to a release are available. Check the release
notes and RELEASE_INFO.txt for the Ubuntu version and architecture actually
built and tested. The current macOS downloads do not imply Intel-native support.
Version 2.3.0 has no prebuilt Ubuntu 22.04 package; 22.04 remains a self-build
option, not a tested binary target for this release.
The [build record](docs/NATIVE_BUILD_PROVENANCE.md) identifies the source revision
for each platform. The Ubuntu label is the author's confirmed build/test host,
not a promise of binary compatibility with earlier Ubuntu releases.

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

`GLIBC_2.38 not found` or a similar GLIBC version error indicates a platform
mismatch; do not replace the system GLIBC manually. AppImage packaging does not
remove the build's GLIBC baseline. An AppImage can
need executable permission (`chmod +x downloaded-file.AppImage`). Extract tar.gz
packages completely and use the supplied launcher.

An offline installation may still need distribution libraries that are not
installed yet. Arrange those APT packages before disconnecting. Serial access
needs the appropriate logged-in user's device permissions; do not run the
simulator as root as a workaround.

### Ubuntu 22.04 self-build

No Ubuntu 22.04 installer is provided for 2.3.0. On Ubuntu 22.04 itself (including
a 22.04 VM), extract the full source archive and run:

```bash
bash scripts/build-v2-ubuntu-22.04.sh
```

The wrapper requires Python 3.12+ and offers a toolchain setup when needed;
review any prompts to add repositories or install packages. It uses an isolated
environment and checks bundled ELF libraries against GLIBC 2.35. Full tests and
native installation checks must pass locally. The script is retained, but a
successful 22.04 build is not claimed for this release. See the
[build instructions](docs/v2_release_scripts.md#ubuntu-2204-self-build).

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
display, not transmission. Follow the [first-start walkthrough](docs/FIRST_START.md)
for a local simulation and receiver check without Internet or provider keys.

For KVM/autostart use, wait for the graphical desktop session. Window geometry
recovery helps when a display appears later but cannot repair a graphics-driver
failure or create a missing desktop session. Test the actual switching sequence.

## Licensing

Original project code uses MPL 2.0. Third-party libraries and chart data have
separate terms. See [release licensing](docs/RELEASE_LICENSING.md). Install only
the version-matched packages listed on the release page; an updated guide does
not replace native build or platform validation.
