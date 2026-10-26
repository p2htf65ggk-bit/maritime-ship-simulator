# Offline operation

Offline means no Internet. Local simulation, routes, instruments and configured
UDP/TCP/serial connections remain available. Google 3D is online-only. Selecting
an offline basemap alone does not enable Offline mode.

## Save Protomaps areas

1. While online, open **Settings > Offline > Protomaps > Add Area...**.
2. Use **Navigate** to pan/zoom. Choose a detail level, then draw rectangles or
   polygons. Switch back to Navigate between shapes or polygon corners.
3. Close each polygon, then **Continue** to queue the areas in the Offline table.
4. Use **Download** or **Download Queued Areas** and wait for completion.
5. Select the available Protomaps source on the 2D map and enable Offline mode.

Saved areas are shown as reference coverage while adding new areas. Existing
tiles shared by overlapping areas are reused. Detail levels are Harbour,
Regional and Overview; higher detail needs more data. Planned areas show tile
counts, not an unreliable MB estimate. Stored areas show actual content size.

## S57 / ENC charts

Under **Settings > Offline > S57 / ENC**, import unencrypted `.000` cells, ZIP
exchange sets or a folder containing cells. Keep numbered updates beside their
base cell, or together in the exchange set. Encrypted S63 is not supported.
Compiled chart formats require their original unencrypted source cells.

While online, **Download NOAA...** offers a searchable catalogue and **Select All
Visible**. Missing/outdated cells are downloaded and imported in the background.
Current installed revisions are skipped. Individual failures do not cancel the
remaining batch; completed cells survive cancellation. Preserve the result/log
if some cells fail. Local imports remain available without Internet.

Select **S-57 (Experimental)** on the 2D map. Use **Layers** for object groups and
**Depth & Safety...** for the simulator's contour/depth display settings. Cell
visibility is managed separately in the offline table. This is simplified
S52-oriented portrayal, not a certified ECDIS, chart-freshness service or grounding
protection system. Water-mask classification identifies surfaces, not safe depth.

## Traffic and Avoid Land

Generated/Synthetic traffic works offline. With **Avoid Land** enabled it needs
usable local water/land geometry. Protomaps downloads retain source geometry;
the needed masks are built locally in the background. With S57 selected,
enabled ENC cells provide the mask independently of current map zoom or layer
visibility. Initial preparation can take time for a large traffic radius.

Outside detailed coverage, disable Avoid Land to place traffic unrestrictedly.
This allows land crossings deliberately. A coarse world outline is not harbour
detail. Live AIS/weather are Internet services; use generated/synthetic traffic
and manual Environment values for a reproducible disconnected demonstration.

## Environmental data offline

With **Depth > Chart-based**, enabled installed S57 cells at own ship supply
depth even when another basemap or zoom level is displayed. Offline Mode does
not request GEBCO data. If local S57 depth is unavailable, the last effective
depth becomes the manual fallback; the requested chart source recovers when
usable data return. Vessel Draft is subtracted from chart water depth only.

Online wind/current/weather requests pause in Offline Mode. Their last valid
values are retained and wind/current allow manual adjustment. Select Manual
explicitly to keep a fixed test environment rather than automatically recovering
the requested online source after reconnecting.

## Manage and transfer maps

The Protomaps table supports rename, renew, detail changes, deletion and
multi-area export/import. Deleting one overlapping area keeps shared data needed
by retained areas. Renewal/detail changes need Internet; rename/delete/export
work offline. Existing completed data is retained if an update fails.

Use area export/import for transfer to another computer, including a USB drive.
Choose a writable destination with sufficient free space and wait for the
completion message before ejecting it. Configuration JSON stores settings and
file references, **not** the chart bytes. Keep any exported chart package subject
to its original data-provider terms. Import S57 source cells/exchange sets on the
destination separately.

Saved maps are under `offline-packages` in the [user configuration directory](../README.md#configuration-and-offline-operation).
Generated mask data is cached under `geodata-cache` and can be regenerated from
saved geometry. Back up maps and operator configuration separately; exported
configuration can contain provider credentials.

The [illustrated guide](manual/Maritime_Ship_Simulator_User_Guide_2.3.1.pdf)
contains the complete offline management workflow and screenshots.
