# Changelog

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
