# Hourly history backfill

Status: implemented in version 0.4.0.

## Goal

Import at most three calendar years of confirmed hourly readings for each configured consumption place without overwhelming the VS Chrudim portal or corrupting Home Assistant statistics.

## Execution

The worker starts after the first successful integration setup and resumes after a restart when a checkpoint exists.

1. Scan backwards from today to three calendar years ago.
2. Request at most 31 inclusive days at a time using the portal's verified `U` (custom range) filter, visible and hidden `GraphFilter1` dates, and `btnRenew` action.
3. Require a CSV with the documented `MERIDLO;CAS;STAV` header and validate that every non-empty response overlaps the requested range. Reject a response that is merely a recent-data fallback.
4. Import completed hours idempotently under the real meter sensor statistic ID. The absolute physical register is used for both statistic state and sum, so independently resumed ranges remain consistent.
5. Persist only a non-sensitive checkpoint: next range end, scan start, progress counters, timestamps and a generic error. Do not persist readings, session cookies or credentials in the checkpoint.
6. Serialize history and normal polling through one API lock, pause briefly between blocks, and retry each failed block with the configured retry count and delay. A later successful coordinator update resumes a failed scan.

## Energy dashboard contract

`sensor.<place>_meter_state` remains the source sensor with `device_class: water`, `state_class: total_increasing`, and unit `m³`. History is imported as an internal Recorder statistic with this same entity ID, because Home Assistant disables entity-based price tracking for external statistics. `sensor.<place>_water_price` remains `CZK/m³` and can therefore be selected as the current price entity; historic tariff changes are deliberately out of scope until a dated tariff source is available.

## Acceptance checks

- A one-month dry run returns hourly values in the requested month.
- A duplicate run creates no duplicate statistics.
- A restart resumes from the persisted checkpoint.
- A meter reset never contributes negative consumption.
- Daylight-saving transitions retain every portal timestamp without inventing a missing hour.
