# Privacy and external services

Configuration, logs, instrument layouts and offline/imported charts are stored
in the user's application-data directory. Complete configuration exports can
include Google/AIS keys and BarentsWatch client credentials. These are not
encrypted secret vaults; restrict access and do not post them publicly.

Online map/3D requests identify the viewed area to their provider. Live AIS uses
credentials and geographic subscriptions/filtering; live atmospheric data uses
own-ship position with Open-Meteo for wind, surface current and atmospheric data.
Chart-based depth first queries installed S57 data locally; a GEBCO fallback
request identifies the sampled position to GEBCO/BODC. Configuration exports
also include an entered Open-Meteo customer key and must be kept private.
Explicit NOAA catalogue/download operations
contact NOAA. Provider terms, quotas and charges apply separately.

Offline mode pauses Internet-backed runtime maps, AIS, weather and GEBCO depth,
not local
LAN/serial traffic. It is not a firewall and does not prohibit every explicit
online action. Disconnect Internet/apply host policy for a strictly disconnected
test and prepare usable local charts/water coverage first.

Offline depth uses installed S57 data or its held manual fallback. Wind/current
retain their last values and allow manual adjustments while requested online
sources are unavailable. Explicit Manual disables that channel's automatic queries.

Map exports do not intentionally contain provider credentials, but area names,
coverage, route files, screenshots and captures can identify a test location.
Share only reviewed, redacted examples. Author contact details in original
prebuilt applications are retained with the author's permission; development
contact for the public source is through GitHub.
