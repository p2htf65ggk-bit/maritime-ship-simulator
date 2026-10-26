# First start

Start with local simulation, then add external equipment or online services.
No Google API key or Internet connection is needed for the first five steps.

1. Install the matching [unsigned package](../INSTALLATION.md). Start the
   application and check **About** for version **2.3.1**. Existing installations
   retain their user settings; review these before connecting a receiver.
2. Open **Settings > Start-up** and review the saved defaults. Do not enable
   automatic output to a real equipment network until its destination is known.
3. Choose **Ship Control**, select a vessel, and try the engine telegraph,
   rudder and bow thruster. Double-click a control slider to reset its demand.
   Thruster response ramps up and becomes less effective with forward speed.
4. Try **Track Control** with the supplied New York to Baltimore route. The
   default is 10 kn planned leg speed, planned speed/turn rate enabled,
   auto-scroll enabled and Reverse at route end. These are factory defaults,
   not overrides for an existing saved configuration.
5. Try **Synthetic AIS Traffic Generator > Add Target** or **Add Encounter**.
   Without downloaded water-mask data, turn **Avoid Land** off. Targets can then
   be placed anywhere, including on land; this is intentional for offline use.

## See detailed maps

Use **Settings > Offline** while online to add Protomaps areas, or import ENC
cells under S57. Then choose the available source on the 2D map. A coarse world
outline is included; it is not a detailed navigational chart. S57 is experimental,
not a certified ECDIS. Offline mode does not make Google 3D imagery available.

For Google 3D, open the small **API KEY** control in the 3D view, choose the
provider, and supply your own appropriately enabled/restricted key. Provider
terms, billing and quotas are separate from this free application. Do not share
exported configurations publicly if they contain credentials.

## Choose environmental sources

Under **Environment**, choose **Manual** to set values yourself. **Depth >
Chart-based** prefers installed S57 cells at own ship, then GEBCO online, then
the last effective manual depth. Import a local ENC first for offline chart
depth. Set the vessel model's **Draft**; the transducer is at keel level and
Draft is subtracted once from chart water depth, never from a manual depth.

Wind, Current and Atmospheric data independently offer **Online - Open-Meteo**.
They retain their last values on data loss, allow manual fallback edits and
recover automatically. Explicit Manual stops automatic queries for that channel.
The Open-Meteo access icon opens optional customer credentials. Free endpoint
access is non-commercial only; commercial use needs suitable provider access.

With **Offline Mode**, depth uses installed S57 or its held manual fallback;
online environmental requests stop. GEBCO and model currents are coarse data,
not local harbour surveys or a navigation-safety guarantee.

## Connect a test receiver

Configure one output endpoint in **Settings**. For a same-computer check, start
with UDP unicast to `127.0.0.1` and a free matching receiver port. A configured
endpoint is not proof that the receiver is listening. Check protocol selection,
destination, network interface, firewall and receiver input settings.

For multicast, select the intended interface. A same-computer receiver may need
multicast loopback enabled on its receiving adapter. Only then move the test to
the intended isolated equipment network. Never connect simulated navigation
data to operational equipment without authorization and proper isolation.

**Output Monitor > Freeze** freezes the displayed log only, not transmission.
To stop sending data, disable the output endpoint or stop the simulation.

## Explore further

- **Sensor Control:** set sensor values directly without ship-handling dynamics.
- **External:** receive rudder, engine and thruster commands; check Main/Fallback
  sources, expected XDR/TRC/PRC fields and input-status indicators.
- **Fault Injection:** enable individual test faults deliberately; saved fault
  settings are not automatically activated on startup.
- **Settings > Start-up:** choose which settings are restored on the next start.
- **Configuration export:** keep a reproducible test setup, but treat the file
  as sensitive when it contains provider credentials.

The [illustrated User Guide](manual/Maritime_Ship_Simulator_User_Guide_2.3.1.pdf)
covers modes, menus, context actions, offline maps and troubleshooting.
Support is best effort. For a problem report, include OS/version, application
version, steps to reproduce and a redacted diagnostic log. Never include keys,
client secrets or personal configuration files.
