# VSChrudim watermeter

Home Assistant custom integration for remote water-meter readings shown in the VS Chrudim customer portal. It creates a device for one selected consumption place and sensors for its cumulative meter state and latest measured consumption.

[Česká verze dokumentace](README.cs.md)

## Installation

In HACS, add this repository as a **Custom repository** of type **Integration**, download it, and restart Home Assistant. Then go to **Settings → Devices & services → Add integration → VSChrudim watermeter**.

For a manual installation, copy `custom_components/vschrudim_watermeter` to `/config/custom_components/` and restart Home Assistant.

## Setup and updates

Enter the portal username and password, then select a consumption place. The default update interval is one hour, matching the portal's hourly reading resolution; it can be changed in Reconfigure (minimum 15 minutes). An interval already saved by the user is preserved during upgrades.

The portal records a cumulative state hourly. `Meter state` is a live display
sensor in m³. Completed portal hours are written by the integration's single
external-statistics writer; this prevents the live sensor and the importer from
creating competing Recorder sums. `Latest consumption` is the non-negative
difference between the newest two states; it is not an instantaneous flow
rate.

Set the all-in water/sewerage price in **Reconfigure**. A value of `0` is intentionally the default until you enter your actual tariff. The integration exposes both `Water price` (`CZK/m³`) and `Total water cost` (`CZK`). Changing the integration price restarts the idempotent history scan so existing hourly cost statistics are recalculated with the new tariff.

### Energy dashboard and historical costs

Use the integration-owned statistics when configuring water in the Home
Assistant Energy dashboard:

1. Open **Settings → Dashboards → Energy → Water** and add or edit the water
   source.
2. Select **Water consumption** from **VSChrudim watermeter** as **Water
   consumption**. This is the external statistic ID
   `vschrudim_watermeter:<entry_id>_water_consumption`, not `sensor.*`.
   Recorder requires lowercase statistic IDs, so `<entry_id>` is normalized to
   lowercase when Home Assistant uses an uppercase config-entry ID.
3. Under cost tracking, choose **Use an entity tracking the total costs**.
4. Select **Water cost** from **VSChrudim watermeter** as the total-cost
   statistic. Its ID is `vschrudim_watermeter:<entry_id>_water_cost`.

This selection is important for imported history. Home Assistant's **fixed
price** and **current price entity** modes create a helper that starts at zero
and calculates only future live changes. They do not retroactively price the
hourly readings imported by this integration. The `Water cost` statistic
contains matching hourly cost statistics, so both historical consumption and
its cost are shown. For each interval, the displayed cost change is the
meter-state change multiplied by the price configured in **Reconfigure**.

`Water price` is the current unit-price sensor and is not the cumulative cost
statistic. `Total water cost` remains a convenient live display entity, but is
not an Energy statistic source. If the dashboard already uses a legacy
`Meter state`/`Total water cost` `sensor.*` source or fixed-price mode, use the
one-time rebuild procedure below before switching both selections.

### Correcting legacy Energy statistics

Version 0.4.8 fixes a former double-writer design that could distort day and
month totals. The upgrade does not delete any data automatically. After the
new version has completed one successful update, run this service in
**Developer Tools → Actions** with your integration's config-entry ID:

```yaml
action: vschrudim_watermeter.rebuild_energy_statistics
data:
  entry_id: "your-config-entry-id"
  confirm: true
```

Starting with version 0.4.9, the same explicitly confirmed rebuild can also be
started from the water-meter device page using **Rebuild Energy statistics**
under **Configuration**. The button performs the safe source preflight and
calls the confirmed rebuild. Direct statistics clearing remains service-only.

The service first downloads and validates the complete available portal
history. Only then does it clear the legacy `sensor.*` statistics and the
integration-owned statistics, import a chronological replacement, verify its
latest monotonic sum, and resume normal writes. If download or validation
fails, it does not delete existing statistics. It never deletes the live
sensor states or their ordinary Recorder history.

`vschrudim_watermeter.clear_energy_statistics` has the same `entry_id` and
`confirm: true` requirement but only clears those Energy statistics and leaves
the writer paused. Use it only when you intentionally want the Energy series
empty before a later rebuild.

## Historical data

After setup, the integration automatically scans up to three calendar years backwards in inclusive 31-day HTTP blocks. Every portal reading remains an individual hourly statistic; the blocks only reduce the number of requests and do not aggregate the data. The integration uses the same custom-date WebForms controls as WebDownloader, prefers the validated CSV response and falls back to the rendered measured-state table when the portal omits its export control. It validates that each non-empty result overlaps the requested range, imports only completed hours, and checkpoints progress after every block. An interrupted or failed scan resumes from its saved cursor; portal requests are serialized with normal polling and retried using the configured recovery settings.

Only progress dates and counters are stored in the integration's `.storage` record. Customer readings are written to Home Assistant's Recorder statistics and are not duplicated in the progress store.

Use **Retry history download** in the device's **Diagnostics** section to
request an immediate portal update and then resume a saved backfill or start a
new reconciliation of the available hourly history. It is not **Rebuild Energy
statistics**: the retry does not delete any statistics or live-sensor history.
Use **Test download** in the same section when you only want to verify the
login and a current portal download. It neither starts a history backfill nor
writes or changes Energy statistics.
`Data available through` always reports the latest timestamp actually returned
by VS Chrudim; the integration does not assume an allowed portal delay.
Home Assistant renders that timestamp entity relatively. **Latest portal
reading** therefore displays the same value as an exact local date and time.

Repeated portal responses are merged by timestamp, with a corrected value
replacing the older value at that timestamp. The external-statistics builders
also produce a single point per completed hour under the integration-owned
statistic IDs, so retrying does not create a second statistic series.

Diagnostic entities show:

- one source state: `ok`, `delayed_data`, `authentication_required`, or
  `error`, with the exact last successful download time and method (CSV link,
  WebForms postback/submit, or HTML table),
- the relative age of the newest portal timestamp (`Data available through`)
  and its exact local date and time (`Latest portal reading`),
- the time and result of the latest update attempt (`Last update attempt`),
- the number of currently missing hourly readings and their oldest timestamp,
- data-quality counters for the most recent download: returned readings,
  duplicate timestamps merged, and gaps recovered by retry downloads,
- three-year history progress, imported-hour count and the last backfill error (`History backfill status`).

`Mark portal data as delayed after` is optional and defaults to `0` (disabled).
It only changes the source-state diagnostic and never treats old portal data as
a failed update. Notifications are created only when authentication is needed,
when gaps remain after the configured retries, or when either condition returns
to normal; a delayed timestamp alone never creates a notification.

The downloaded diagnostics additionally include a newest-first rolling history
of the last 30 normal download attempts. Each record contains only safe timing,
result, retrieval-method, count and sanitized-error information. It survives a
Home Assistant restart and contains no credentials, cookies, portal HTML, CSV
content, consumption-place identifiers or request URLs.

## Availability notifications and missing readings

The integration uses Home Assistant persistent notifications only on state
transitions. By default it reports the portal as unavailable after three
consecutive failed updates, reports an authentication problem immediately, and
creates one recovery notification after either problem returns to normal.
Authentication errors use the standard reauthentication flow.

Every successful download is merged with readings already seen during the current runtime. Internal hourly gaps in the **current portal response range** trigger up to two repeated downloads with a configurable delay. Historical gaps discovered by the separate backfill are not reported as a current-download outage, because a normal portal request cannot repair them. Corrected portal values replace the older value with the same timestamp. If current-range gaps remain after those attempts, one persistent notification lists their count and a short timestamp preview; a recovery notification is created once the readings are filled. Czech spring DST's nonexistent 02:00 hour is not treated as missing. All thresholds, notifications, attempt counts and delays can be changed under **Reconfigure**.

Notification transition flags survive a Home Assistant restart, so an unchanged
problem does not create duplicate alerts. The state contains no portal or
customer data, and its persistence is best effort: a storage failure never
prevents an otherwise valid portal update.

## Portal compatibility

VS Chrudim supplies an authenticated ASP.NET WebForms website, not a documented public API. The client follows fields, menu links, WebForms postbacks and CSV-export links found in the authenticated HTML, and fails safely when the expected structure is absent. It does not guess REST endpoints or run WebDownloader.

If the portal skips its consumption-place list and opens a previously selected
reporting/detail context directly, the integration continues through that
context only after its portal identifiers match the configured consumption
place. The confirmation accepts the portal's stable ASP.NET control suffix in
either its form `name` or HTML `id` (including rendered text); an absent,
unverified grid is never treated as permission to use another place's data.

The portal's current custom-range controls are required for historical backfill.
For measured-state downloads the integration supports verified direct CSV
links, documented WebForms submit controls, verified `__doPostBack` export
LinkButtons, and a deterministic table fallback with Czech date and meter-state
headings. It never runs arbitrary JavaScript. If the provider changes or
removes all usable structures, normal polling remains isolated from the failed
backfill and the diagnostic status reports the protocol error.

Each portal HTTP request has a 45-second deadline and accepts at most 12 MiB of
response data. These guards fail a malformed or stalled response safely without
changing normal polling, backfill or Energy-statistics processing. A running
backfill is cancelled and checkpoints its progress when the integration is
unloaded, then resumes from that checkpoint after setup.

## Security

The username and password are stored in the Home Assistant ConfigEntry (and therefore may be present in an encrypted Home Assistant backup). Session cookies are not persisted; tokens and credentials are redacted from diagnostics and never logged.

Brand icons are shipped inside `custom_components/vschrudim_watermeter/brand/`, which is the current Home Assistant inline-brand location. Home Assistant displays them locally. Some HACS versions still show a placeholder for custom repositories because their downloads panel has not yet adopted Home Assistant's authenticated local brands proxy; the icon files are nevertheless included in every release and installation.

## Disclaimer

This integration is independent and is not an official product of, or supported by, Vodárenská společnost Chrudim. The web interface used by it is not publicly guaranteed and may change without notice.
