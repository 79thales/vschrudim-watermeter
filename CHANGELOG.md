# Changelog

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
