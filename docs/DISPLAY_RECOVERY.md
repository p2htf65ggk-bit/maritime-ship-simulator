# Displays, KVM and startup

Start the simulator within a running graphical desktop session. A display may
appear later through a KVM, but the application cannot create a missing desktop
session or recover a failed graphics driver.

The simulator monitors display/work-area changes and rechecks delayed KVM
changes. If a visible application window becomes unreachable or too large for
the available desktop, it is moved/resized into a usable area. Valid positions
on a second monitor, full-screen/maximized state and intentionally minimized or
hidden windows are preserved. Geometry uses logical coordinates, including
desktop scaling and monitors to the left of the primary display.

## Check the actual setup

1. Start the graphical desktop and simulator while the KVM points elsewhere.
2. Switch to the simulator; verify its title bar and window controls are visible.
3. Switch away/back during operation and check detached map/3D windows too.
4. Minimize a window deliberately; recovery should not restore it unexpectedly.

If a window is still unreachable, use the operating system's window-management
controls first. Collect a redacted `startup.log` plus display configuration
before and after switching. Do not assume geometry is the cause: a blocked UI
thread, GPU failure or window-manager focus policy can produce similar symptoms.

Automated geometry/lifecycle tests do not qualify every real KVM, EDID adapter,
display driver or window manager. See [validation](VALIDATION.md).
