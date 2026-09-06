# Hourly history backfill plan

## Goal

Import at most three calendar years of confirmed hourly readings for each configured consumption place without overwhelming the VS Chrudim portal or corrupting Home Assistant statistics.

## Preconditions

The authenticated portal must first be manually verified with a real account: after navigating from the selected place through **Dálkové odečty vodoměru → Naměřené stavy**, a custom range must return a CSV whose earliest and latest timestamps match the selected range. This is intentionally a release gate: the WebDownloader source records previous failures where the portal accepted a range in the UI but returned only recent hours.

## Scheduled execution

After that verification, expose an opt-in `Download history` button. Its resumable worker runs at most once per day between 01:00–05:00 local HA time, never during a normal coordinator update.

1. Start at `max(last_confirmed_timestamp + 1 hour, now - 3 years)`.
2. Request one calendar month at a time using the portal's verified `U` (custom range) filter, `GraphFilter1` dates and `btnRenew` action.
3. Require a CSV with the documented `MERIDLO;CAS;STAV` header and validate that its range overlaps the requested month. Reject a response that is merely a recent-data fallback.
4. Deduplicate by `(place, timestamp)`, calculate only non-negative meter-state deltas, then add external long-term statistics using a stable statistic ID. Never rewrite values whose source timestamp has already been confirmed.
5. Persist only a non-sensitive checkpoint: place identifier, next month, last confirmed timestamp and a failure count. Do not persist session cookies or credentials.
6. Stop after one month or 200 HTTP requests per scheduled run; retry the same checkpoint after transient errors with exponential backoff. Authentication failure starts Home Assistant reauthentication and pauses the plan.

## Energy dashboard contract

`sensor.<place>_meter_state` remains the source sensor with `device_class: water`, `state_class: total_increasing`, and unit `m³`. Its external statistic metadata will match those properties. `sensor.<place>_water_price` remains `CZK/m³` and is selected as the current price entity; historic tariff changes are deliberately out of scope until a dated tariff source is available.

## Acceptance checks before enabling it

- A one-month dry run returns hourly values in the requested month.
- A duplicate run creates no duplicate statistics.
- A restart resumes from the persisted checkpoint.
- A meter reset never contributes negative consumption.
- Daylight-saving transitions retain every portal timestamp without inventing a missing hour.
