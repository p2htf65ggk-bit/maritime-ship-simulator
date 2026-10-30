# Installation

The 2.3.2 packages passed all four native runner jobs; the author reports working
native builds. See [validation](docs/VALIDATION.md) for the checks and limits.
They use the normal V2 settings folder, not the HUD beta's separate profile;
back up your current configuration and review output endpoints before starting.

Use the version-specific assets on the project's GitHub Releases page. Do not
download a build-source archive to install the application. Existing application
packages include Python and Qt; end users do not need Git, Python, Inno Setup or
the developer build scripts.

## Choose a package

| System | Installer | Portable alternative |
| --- | --- | --- |
| Windows x64 (tested on Windows 11) | Setup EXE | ZIP, fully extracted before starting |
| Ubuntu 26.04 LTS amd64 | DEB labelled `ubuntu-26.04-amd64` | AppImage or tar.gz with the same label |
| Ubuntu 22.04 compatibility, amd64 | DEB labelled `ubuntu-22.04-amd64` | AppImage or tar.gz with the same label |
| macOS Apple silicon | DMG | Application ZIP |

Only assets actually attached to a release are available. Check the release
notes and RELEASE_INFO.txt for the Ubuntu version and architecture actually
built and tested. The current macOS downloads do not imply Intel-native support.
Additional 22.04 compatibility packages were built and automatically checked on
an Ubuntu 22.04 GitHub runner, including the GLIBC 2.35 gate. The author tested
that build on Ubuntu 26.04. No manual 22.04 desktop test is claimed.
The [build record](docs/NATIVE_BUILD_PROVENANCE.md) identifies the source revision
for each platform. The current packages are built on 26.04 and 22.04 runners;
the author uses Ubuntu 26.04.1 for desktop testing. This is not a promise of
compatibility with every desktop.
Windows versions other than Windows 11, Intel Macs and older macOS versions
have not been qualified for these packages. A source build on another platform
is separate from the compatibility of the supplied binaries.

## Verify the download

Compare SHA-256 with the manifest for that exact release. This detects a changed
or incomplete file, not a trusted developer signature. Check the download source
as well. Substitute the actual filename, including version and compatibility label.

```powershell
Get-FileHash .\Maritime-Ship-Simulator-V2-2.3.2-windows-setup.exe -Algorithm SHA256
```

```bash
shasum -a 256 Maritime-Ship-Simulator-V2-2.3.2-macos.dmg
sha256sum Maritime-Ship-Simulator-V2-2.3.2-ubuntu-26.04-amd64-installer.deb
```

## Windows

Microsoft Security Intelligence completed its final review on 30 October 2026:
**Not malware**; the analyst reported the detection removed. The reviewed
`Ship Simulator V2.exe` has SHA256
`d1d87b7504268843224391d032434bd8ce6c7837defeeca6b9b8f5c9447e2cfe`.
This exact executable is distributed unchanged. Update Defender's security
intelligence before retrying a previously detected file. This finding applies
to this file, not to every dependency, future build or application-control policy.
It does not remove the unsigned-package limitations described below.

Close existing instances, run the setup EXE or extract the portable ZIP to a
writable directory, then start the application and check About. The packages
are unsigned. Download only from the official release or an author-provided
Actions test artifact, verify SHA-256 against that exact package's manifest and
proceed only if you trust the source. A matching hash is not a malware verdict.

### Blue "Windows protected your PC" window

For the expected, trusted installer download:

1. Click **More info** (German Windows: **Weitere Informationen**).
2. Check the application filename, then click **Run anyway** (German Windows:
   **Trotzdem ausführen**). This button normally appears only after expanding
   More info; the initial window shows only **Don't run**.
3. Continue with the setup wizard. An **Unknown publisher** indication is
   expected for the unsigned installer, but does not prove that a file is safe.

If **Run anyway** is still unavailable after expanding the details, or a policy
error appears, stop and record the exact message or consult your administrator.
These steps apply to SmartScreen's unrecognized-app warning only, not a Defender
malware alert or a Smart App Control block. Do not disable Windows security globally.
Allow only the required traffic on the intended trusted network profile.

[Microsoft's SmartScreen guidance for unsigned apps](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
explains why a new unsigned download can show this warning.

SmartScreen's unknown-publisher warning and a Defender threat detection are
different. If Defender quarantines a file, record its detection name and check
the exact download hash. Do not disable protection or restore it solely because
this release is unsigned. Report the detection through GitHub and, where
appropriate, [Microsoft's file-submission portal](https://www.microsoft.com/en-us/wdsi/filesubmission).
The portable ZIP is not a guaranteed workaround if its application EXE is flagged.

Smart App Control is another, separate Windows check. It can block unsigned
files with insufficient reputation, including Inno Setup's temporary executable
and the installed application, even with current Defender definitions and no
malware detection. Error 4551 means an application-control policy blocked the
file. A portable ZIP does not establish publisher trust for its executable.
Record the exact blocked filename and error. These unsigned packages are not
qualified for use with Smart App Control enabled; do not disable protection or
create blanket exclusions as an installation requirement.

## Ubuntu

For Ubuntu 22.04, use a filename containing **`ubuntu-22.04-amd64`**. These
compatibility packages can also be used on newer amd64 Ubuntu systems; the
author tested them on 26.04. The `ubuntu-26.04-amd64` files require the newer
baseline and are not a 22.04 option. All three compatibility formats
contain the same application, and none removes the need for system libraries.

Install the DEB using APT so its declared dependencies can be resolved:

```bash
sudo apt install ./Maritime-Ship-Simulator-V2-2.3.2-ubuntu-26.04-amd64-installer.deb
```

For the 22.04 compatibility DEB instead:

```bash
sudo apt install ./Maritime-Ship-Simulator-V2-2.3.2-ubuntu-22.04-amd64-installer.deb
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

To build instead of using the supplied compatibility downloads, use Ubuntu
22.04 itself (including a 22.04 VM), extract the source and run:

```bash
bash scripts/build-v2-ubuntu-22.04.sh
```

The wrapper requires Python 3.12+ and offers a toolchain setup when needed;
review any prompts to add repositories or install packages. It uses an isolated
environment and checks bundled ELF libraries against GLIBC 2.35. Full tests and
native installation checks must pass locally. The published compatibility
packages were built from the exact revision linked in the
[build record](docs/NATIVE_BUILD_PROVENANCE.md). See the
[build instructions](docs/v2_release_scripts.md#ubuntu-2204-self-build).

## macOS

The packages have no Apple Developer ID signature or notarization. Verify the
download source and the package hash before deciding whether to trust the app.

1. Copy the application from the DMG into **Applications**, or extract the
   application ZIP completely.
2. Try opening the app once. If macOS blocks it because the developer cannot be
   verified, close the warning.
3. Open **System Settings > Privacy & Security** (German macOS:
   **Systemeinstellungen > Datenschutz & Sicherheit**) and scroll to **Security**.
4. For the simulator, choose **Open Anyway** (**Dennoch öffnen**). Some macOS
   versions first show an **Open** action. Confirm the app name and authenticate
   when asked, then launch the application again if necessary.

The app-specific approval normally appears only after the first blocked launch.
If it has expired, try opening the app again before returning to these settings.
Do not apply these steps to a malware or damaged-app warning; stop and report
that exact message instead. Never disable Gatekeeper globally. See
[Apple's instructions](https://support.apple.com/en-gb/guide/mac-help/mh40617/mac).
Grant local-network access when needed for configured network inputs/outputs.

## Existing settings and first start

Close the previous version and back up user configuration before upgrading.
Install/extract 2.3.2, check About, and review outputs before connecting equipment.
The existing user-data location is retained. Older configurations without the
new environment selectors remain Manual; online sources are opt-in. Configuration
exports can contain provider keys, including an entered Open-Meteo customer key.
Do not post those exports publicly.

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
