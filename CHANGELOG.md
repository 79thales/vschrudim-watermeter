# Changelog

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
