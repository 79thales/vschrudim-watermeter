# VSChrudim watermeter

Home Assistant custom integration for remote water-meter readings shown in the VS Chrudim customer portal. It creates a device for one selected consumption place and sensors for its cumulative meter state and latest measured consumption.

## Installation

In HACS, add this repository as a **Custom repository** of type **Integration**, download it, and restart Home Assistant. Then go to **Settings → Devices & services → Add integration → VSChrudim watermeter**.

For a manual installation, copy `custom_components/vschrudim_watermeter` to `/config/custom_components/` and restart Home Assistant.

## Setup and updates

Enter the portal username and password, then select a consumption place. The default update interval is four hours, matching the portal's documented usual data publication delay; it can be changed in Reconfigure (minimum 15 minutes).

The portal records a cumulative state hourly. The `Meter state` sensor uses `total_increasing` in m³. `Latest consumption` is the non-negative difference between the newest two states. It is not an instantaneous flow rate.

## Portal compatibility

VS Chrudim supplies an authenticated ASP.NET WebForms website, not a documented public API. The client follows fields, menu links, WebForms postbacks and CSV-export links found in the authenticated HTML, and fails safely when the expected structure is absent. It does not guess REST endpoints or run WebDownloader.

Historical backfill is intentionally not implemented in 0.1.0: the desktop source confirms CSV export after portal-side filtering but does not provide a verified direct filter/export protocol suitable for unattended Home Assistant operation. Home Assistant retains state history after installation.

## Security

The username and password are stored in the Home Assistant ConfigEntry (and therefore may be present in an encrypted Home Assistant backup). Session cookies are not persisted; tokens and credentials are redacted from diagnostics and never logged.

## Disclaimer

This integration is independent and is not an official product of, or supported by, Vodárenská společnost Chrudim. The web interface used by it is not publicly guaranteed and may change without notice.
