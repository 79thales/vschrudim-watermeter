# VSChrudim watermeter

Home Assistant custom integration for remote water-meter readings shown in the VS Chrudim customer portal. It creates a device for one selected consumption place and sensors for its cumulative meter state and latest measured consumption.

## Installation

In HACS, add this repository as a **Custom repository** of type **Integration**, download it, and restart Home Assistant. Then go to **Settings → Devices & services → Add integration → VSChrudim watermeter**.

For a manual installation, copy `custom_components/vschrudim_watermeter` to `/config/custom_components/` and restart Home Assistant.

## Setup and updates

Enter the portal username and password, then select a consumption place. The default update interval is one hour, matching the portal's hourly reading resolution; it can be changed in Reconfigure (minimum 15 minutes). An interval already saved by the user is preserved during upgrades.

The portal records a cumulative state hourly. The `Meter state` sensor uses `total_increasing` in m³ and is the statistic to select in **Settings → Dashboards → Energy → Water consumption**. Completed portal hours are imported under that real sensor ID, so the Energy dashboard can use the historical readings and still allow an entity-based current price. `Latest consumption` is the non-negative difference between the newest two states; it is not an instantaneous flow rate.

Set the all-in water/sewerage price in **Reconfigure**. A value of `0` is intentionally the default until you enter your actual tariff. The integration exposes both `Water price` (`CZK/m³`) and `Total water cost` (`CZK`). Because Home Assistant's fixed/current-price helper only calculates future live state changes, select `Total water cost` under **Use an entity tracking the total costs** to see costs for the hourly history imported by this integration. Changing the integration price restarts the idempotent history scan so existing hourly cost statistics are recalculated with the new tariff.

## Historical data

After setup, the integration automatically scans up to three calendar years backwards in inclusive 31-day HTTP blocks. Every portal reading remains an individual hourly statistic; the blocks only reduce the number of requests and do not aggregate the data. The integration uses the same custom-date WebForms controls as WebDownloader, prefers the validated CSV response and falls back to the rendered measured-state table when the portal omits its export control. It validates that each non-empty result overlaps the requested range, imports only completed hours, and checkpoints progress after every block. An interrupted or failed scan resumes from its saved cursor; portal requests are serialized with normal polling and retried using the configured recovery settings.

Only progress dates and counters are stored in the integration's `.storage` record. Customer readings are written to Home Assistant's Recorder statistics and are not duplicated in the progress store.

Diagnostic entities show:

- the newest timestamp contained in the latest successful portal download (`Data available through`),
- the time and result of the latest update attempt (`Last update attempt`),
- three-year history progress, imported-hour count and the last backfill error (`History backfill status`).

## Availability notifications and missing readings

The integration uses Home Assistant persistent notifications. By default it reports the portal as unavailable after three consecutive failed updates, replaces the same notification on further failures, and dismisses it automatically after recovery. Authentication errors use the standard reauthentication flow instead.

Every successful download is merged with readings already seen during the current runtime. Internal hourly gaps trigger up to two repeated downloads with a configurable delay. Corrected portal values replace the older value with the same timestamp. If gaps remain, one persistent notification lists their count and a short timestamp preview; it disappears when the readings are filled. Czech spring DST's nonexistent 02:00 hour is not treated as missing. All thresholds, notifications, attempt counts and delays can be changed under **Reconfigure**.

## Portal compatibility

VS Chrudim supplies an authenticated ASP.NET WebForms website, not a documented public API. The client follows fields, menu links, WebForms postbacks and CSV-export links found in the authenticated HTML, and fails safely when the expected structure is absent. It does not guess REST endpoints or run WebDownloader.

The portal's current custom-range controls are required for historical backfill. If the provider changes or removes them, normal polling remains isolated from the failed backfill and the diagnostic status reports the protocol error.

## Security

The username and password are stored in the Home Assistant ConfigEntry (and therefore may be present in an encrypted Home Assistant backup). Session cookies are not persisted; tokens and credentials are redacted from diagnostics and never logged.

Brand icons are shipped inside `custom_components/vschrudim_watermeter/brand/`, which is the current Home Assistant inline-brand location. Home Assistant displays them locally. Some HACS versions still show a placeholder for custom repositories because their downloads panel has not yet adopted Home Assistant's authenticated local brands proxy; the icon files are nevertheless included in every release and installation.

## Disclaimer

This integration is independent and is not an official product of, or supported by, Vodárenská společnost Chrudim. The web interface used by it is not publicly guaranteed and may change without notice.
