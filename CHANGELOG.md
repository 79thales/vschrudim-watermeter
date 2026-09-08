# Changelog

## 0.4.16 - 2026-09-08

- Prevent historical backfill gaps from being reported as missing readings in
  the portal's current download range.
- Preserve notification transition state across a Home Assistant restart,
  while treating its storage as best effort so it cannot block a normal update.
- Bound each portal response to 12 MiB and each request to 45 seconds, and
  checkpoint a running history backfill before integration unload.
- Keep normal polling, historical reconciliation, live sensor history and
  Energy-statistics behavior unchanged for valid portal responses.

## 0.4.15 - 2026-09-08

- Fix the Home Assistant compatibility regression test for the isolated
  no-side-effect download check.

## 0.4.14 - 2026-09-08

- Add a concise source-status diagnostic, gap diagnostics and per-download
  data-quality counters, including the actual retrieval method and exact
  successful-download time.
- Add a device **Test download** button that verifies login and the current
  download without starting history work or changing Energy statistics.
- Make source and missing-reading notifications transition-based, while keeping
  portal data age diagnostic-only unless the user explicitly enables a delay
  status threshold.

## 0.4.13 - 2026-09-08

- Add an exact local timestamp diagnostic for the newest reading returned by
  the VS Chrudim portal, alongside Home Assistant's relative timestamp view.

## 0.4.12 - 2026-09-08

- Automatically establish one fresh authenticated portal session and replay
  the complete request when an existing session expires.
- Preserve normal Home Assistant reauthentication for rejected credentials and
  surface a second expired-session response instead of retrying indefinitely.

## 0.4.11 - 2026-09-08

- Add a separate device button and action to immediately retry the portal
  download and resume or restart the idempotent history reconciliation.
- Keep the retry entirely separate from the destructive Energy-statistics
  rebuild; it never clears statistics or the live sensor history.
- Deduplicate repeated portal readings by timestamp before creating the single
  external statistic for each hour.

## 0.4.10 - 2026-09-08

- Reuse the portal's already selected reporting context when the consumption-
  place grid is temporarily absent, but only after verifying that its hidden
  identifiers match the configured place.
- Classify an unverified missing place list separately instead of reporting
  the misleading low-level grid-parser error.
- Add regression coverage for the verified reporting fallback and prevention
  of cross-place data reuse.

## 0.4.9 - 2026-09-08

- Fix the Energy-statistics rebuild failure caused by an unsupported cost
  verification argument.
- Treat a lower register value as a new meter baseline instead of attributing
  the complete replacement-meter state to one hour of consumption.
- Serialize clear and rebuild maintenance operations to prevent concurrent
  destructive Recorder changes.
- Add a configuration-category device button for explicitly rebuilding Energy
  statistics after the complete source preflight.
- Add regression coverage for rebuild verification, meter replacement and the
  new maintenance button.

## 0.4.8 - 2026-09-07

- Support verified ASP.NET WebForms `__doPostBack` export LinkButtons while
  preserving the complete successful form payload.
- Improve the rendered measured-state table fallback for nested and wrapped
  ASP.NET markup when no usable export action is available.
- Treat a valid portal response with an unchanged newest reading as a
  successful update; source-data age remains diagnostic information only.
- Persist a bounded, privacy-safe history of recent download attempts in
  diagnostics, including retrieval method, duration and sanitized errors.
- Move Energy water consumption and cost to the integration-owned external
  statistics writer, preventing duplicate `sensor.*` sums from distorting day
  and month totals.
- Add explicitly confirmed services to clear or preflight-validate and rebuild
  only VSChrudim Energy statistics.

## 0.4.7 - 2026-09-07

- Treat a filtered range without an export action as the beginning of the
  portal's available history only when newer hourly readings have already
  established that boundary.
- Complete the resumable backfill at that boundary instead of repeatedly
  reporting an error for dates before the first available smart-meter reading.
- Dismiss a stale incomplete-history notification as soon as a saved backfill
  resumes.

## 0.4.6 - 2026-09-07

- Submit the complete measured-state filter form when changing to a custom
  period and when applying its dates, matching the working WebDownloader
  browser flow.
- Preserve all successful portal input controls required to render the
  filtered hourly result and its export action.
- Add regression coverage for complete custom-period WebForms payloads.

## 0.4.5 - 2026-09-07

- Fix integration setup by importing the configured-price constants used by
  the history cost backfill coordinator.
- Add a Home Assistant compatibility regression check for the coordinator's
  price configuration dependencies.

## 0.4.4 - 2026-09-07

- Fall back to the rendered measured-state table when the portal omits its CSV
  link and WebForms export control.
- Port the table-selection, Czech date and meter-state parsing approach from
  WebDownloader while keeping customer values out of logs and diagnostics.
- Keep the last successful meter readings available during transient update
  failures, keep the locally configured water price independent of the portal,
  and leave diagnostic entities available to explain the failure.
- Add a cumulative total-water-cost entity and import matching hourly cost
  statistics, because Home Assistant does not retroactively price backfilled
  volume statistics.
- Add regression coverage for direct HTML-table readings and API fallback.

## 0.4.3 - 2026-09-07

- Validate export responses with the same CSV-column normalization used by the
  parser, including quoted, BOM-prefixed and Czech accented headers.
- Distinguish a missing export control from an export response with invalid
  content without logging response data or customer values.
- Add regression coverage for the accepted header variants and both failure
  categories.

## 0.4.2 - 2026-09-07

- Match the working WebDownloader export flow by supporting both direct export
  links and WebForms export buttons.
- Submit the complete current filter form, including its text fields, selected
  values and ASP.NET state, when the portal exposes export as a button.
- Preserve authentication and CSV-header validation for every candidate.
- Add regression coverage for the WebForms export-button response.

## 0.4.1 - 2026-09-07

- Recognize the portal's `DocumentShow.aspx` history export even when its link
  text and URL do not contain the word `CSV`.
- Continue to accept export responses only after verifying the expected
  `MERIDLO;CAS;STAV` header.
- Add regression coverage for accepted and invalid `DocumentShow.aspx`
  responses.

## 0.4.0 - 2026-09-07

- Change the default polling interval to one hour while preserving explicitly
  configured intervals.
- Import completed cumulative readings under the real `Meter state` entity
  statistic so it is immediately selectable as water consumption in Energy.
- Preserve entity-based `CZK/m³` price support in the Energy water settings.
- Add an automatic, resumable three-year history scan in verified 31-day HTTP
  ranges while retaining every portal record as a separate hourly statistic.
- Persist only backfill progress and counters; readings remain in Recorder.
- Add diagnostic sensors for newest available data, latest update attempt and
  history-backfill progress/errors.
- Notify when a history scan exhausts its retries and dismiss the notification
  after a successful resumed scan.
- Remove consumption-place identifiers and readings from shared diagnostics.
- Add WebForms custom-range and internal-statistics regression coverage.
- Document the current upstream HACS custom-repository icon limitation while
  continuing to ship both inline brand icon resolutions.

## 0.3.4 - 2026-09-07

- Open measured states through the portal's verified
  `/Userdata/ProfileData.aspx` route after selecting a consumption place.
- Validate the presence of the readings period filter before downloading data.
- Support the actual `MainMenu1$btnProfileData` WebForms postback as a fallback,
  including its empty event argument.
- Add regression tests for direct and postback-based readings navigation.

## 0.3.3 - 2026-09-06

- Give every config flow validation and configured account its own cookie jar.
- Prevent a successful config-flow login from leaking its authenticated portal
  state into the newly created runtime client, where the missing login form was
  incorrectly treated as a protocol failure.
- Isolate cookies between multiple VS Chrudim accounts and let Home Assistant
  automatically clean up each config entry's HTTP session.

## 0.3.2 - 2026-09-06

- Fix consumption-place discovery by excluding the portal grid's hidden cells,
  matching the verified WebDownloader implementation.
- Preserve the original capitalization of customer-visible place data.
- Use the verified WebForms `Show$N` postback fallback when a row does not
  expose its inline postback handler.
- Add a regression fixture for the real grid structure with a hidden leading
  cell.

## 0.3.1 - 2026-09-06

- Fix ASP.NET login submission selecting the language image instead of the
  portal's actual `btnLogin` submit control.
- Add a regression test using the current public login-form structure.

## 0.3.0 - 2026-09-06

- Add configurable persistent notifications after repeated source failures.
- Add bounded repeated downloads to recover individual missing hourly readings.
- Merge corrected portal values by timestamp and expose gap/retry diagnostics.
- Add Home Assistant compatibility CI for 2026.8 and 2026.9.

## 0.2.0 - 2026-09-06

- Add `Water price` sensor in CZK/m³ for Home Assistant Energy water costs.
- Document and scope the three-year hourly-history backfill plan.

## 0.1.0 - 2026-09-06

- Initial HACS-ready VSChrudim watermeter integration.
- Config flow, reauthentication, coordinator and water-meter sensors.
- Defensive ASP.NET WebForms/CSV client, diagnostics and parser/calculation tests.
