# VSChrudim watermeter

Home Assistant custom integration for remote water-meter readings shown in the VS Chrudim customer portal. It creates a device for one selected consumption place and sensors for its cumulative meter state and latest measured consumption.

## Installation

In HACS, add this repository as a **Custom repository** of type **Integration**, download it, and restart Home Assistant. Then go to **Settings → Devices & services → Add integration → VSChrudim watermeter**.

For a manual installation, copy `custom_components/vschrudim_watermeter` to `/config/custom_components/` and restart Home Assistant.

## Setup and updates

Enter the portal username and password, then select a consumption place. The default update interval is four hours, matching the portal's documented usual data publication delay; it can be changed in Reconfigure (minimum 15 minutes).

The portal records a cumulative state hourly. The `Meter state` sensor uses `total_increasing` in m³ and is the statistic to select in **Settings → Dashboards → Energy → Water consumption**. `Latest consumption` is the non-negative difference between the newest two states; it is not an instantaneous flow rate.

Set the all-in water/sewerage price in **Reconfigure**. The `Water price` sensor has unit `CZK/m³`; select it under **Use an entity with the current price** in the Water dashboard configuration. A value of `0` is intentionally the default until you enter your actual tariff.

## Availability notifications and missing readings

The integration uses Home Assistant persistent notifications. By default it reports the portal as unavailable after three consecutive failed updates, replaces the same notification on further failures, and dismisses it automatically after recovery. Authentication errors use the standard reauthentication flow instead.

Every successful download is merged with readings already seen during the current runtime. Internal hourly gaps trigger up to two repeated downloads with a configurable delay. Corrected portal values replace the older value with the same timestamp. If gaps remain, one persistent notification lists their count and a short timestamp preview; it disappears when the readings are filled. Czech spring DST's nonexistent 02:00 hour is not treated as missing. All thresholds, notifications, attempt counts and delays can be changed under **Reconfigure**.

## Portal compatibility

VS Chrudim supplies an authenticated ASP.NET WebForms website, not a documented public API. The client follows fields, menu links, WebForms postbacks and CSV-export links found in the authenticated HTML, and fails safely when the expected structure is absent. It does not guess REST endpoints or run WebDownloader.

The implementation plan for hourly history up to three years is in [HISTORY_PLAN.md](HISTORY_PLAN.md). Home Assistant retains state history after installation.

## Security

The username and password are stored in the Home Assistant ConfigEntry (and therefore may be present in an encrypted Home Assistant backup). Session cookies are not persisted; tokens and credentials are redacted from diagnostics and never logged.

## Disclaimer

This integration is independent and is not an official product of, or supported by, Vodárenská společnost Chrudim. The web interface used by it is not publicly guaranteed and may change without notice.
