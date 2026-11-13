"""Bezpečné porovnání hodinových součtů Recorderu bez přístupu k portálu."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

HOUR = timedelta(hours=1)
# Jde o výpočetní limit dotazu, nikoli o předpoklad stáří dat portálu.
MAX_COMPARE_HOURS = 24 * 366 * 4


def _utc(value: object) -> datetime | None:
    """Převést čas Recorderu na UTC; nejednoznačný místní čas neodhadovat.

    Recorder může vrátit epochu v sekundách nebo datetime s pásmem. ISO řetězec
    musí mít rovněž offset. Neplatný, nekonečný a naivní čas vrací None.
    """
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                return None
            return datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            result = datetime.fromisoformat(value)
        else:
            return None
        if result.tzinfo is None or result.utcoffset() is None:
            return None
        return result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _number(value: object) -> float | None:
    """Vrátit jen konečnou číselnou hodnotu; None není nulová spotřeba."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _index(
    rows: Iterable[Mapping[str, object]],
) -> tuple[dict[datetime, dict[str, Any]], dict[str, int]]:
    """Zaindexovat body; kolizní čas nepovažovat za spolehlivý bod.

    Vstup jsou záznamy jedné externí statistiky v m³. Výstup obsahuje počet
    duplicit a vadných/nezaokrouhlených časů bez kopií identifikátorů zdroje.
    """
    points: dict[datetime, dict[str, Any]] = {}
    counts = Counter()
    for row in rows:
        start = _utc(row.get("start"))
        if start is None:
            counts["invalid_timestamp"] += 1
            continue
        if start.minute or start.second or start.microsecond:
            counts["unaligned_timestamp"] += 1
            continue
        if start in points:
            points[start]["duplicate"] = True
            counts["duplicate_timestamp"] += 1
            continue
        points[start] = {
            "sum": _number(row.get("sum")),
            "state": _number(row.get("state")),
            "duplicate": False,
        }
    return points, dict(counts)


def _change(
    points: Mapping[datetime, Mapping[str, Any]], start: datetime
) -> tuple[float | None, str | None]:
    """Změna pouze mezi sousedními hodinovými součty, bez rozdělení mezery."""
    current = points.get(start)
    previous = points.get(start - HOUR)
    if current is None:
        return None, "missing"
    if previous is None:
        return None, "missing_previous"
    if current["duplicate"] or previous["duplicate"]:
        return None, "duplicate"
    if current["sum"] is None or previous["sum"] is None:
        return None, "invalid_sum"
    delta = current["sum"] - previous["sum"]
    if delta < -1e-9:
        return None, "sum_decreased"
    # Odchylky pod numerickou přesností jsou nulový rozdíl floatu, nikoli
    # odhad chybějících dat ani práh pro posouzení skutečné spotřeby.
    return round(max(0.0, delta), 9), None


def _day_hours(day: date, zone: ZoneInfo) -> int:
    """Počet UTC hodin místního dne; přechody DST mají 23 nebo 25 hodin."""
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
    return int((end - start).total_seconds() // 3600)


def _calendar_periods(
    days: list[dict[str, Any]], zone: ZoneInfo, *, yearly: bool
) -> list[dict[str, Any]]:
    """Sečíst výhradně společné hodiny; úplnost zahrnuje celé místní období."""
    grouped: dict[str, dict[str, Any]] = {}
    for day in days:
        current = date.fromisoformat(day["date"])
        first = date(current.year, 1 if yearly else current.month, 1)
        if yearly:
            following = date(current.year + 1, 1, 1)
            label = str(current.year)
        elif current.month == 12:
            following = date(current.year + 1, 1, 1)
            label = first.isoformat()[:7]
        else:
            following = date(current.year, current.month + 1, 1)
            label = first.isoformat()[:7]
        expected = int(
            (
                datetime.combine(following, time.min, zone).astimezone(UTC)
                - datetime.combine(first, time.min, zone).astimezone(UTC)
            ).total_seconds()
            // 3600
        )
        group = grouped.setdefault(
            label,
            {
                "period": label,
                "expected_hours": expected,
                "selected_hours": 0,
                "comparable_hours": 0,
                "a_m3": 0.0,
                "b_m3": 0.0,
            },
        )
        group["selected_hours"] += day["selected_hours"]
        group["comparable_hours"] += day["comparable_hours"]
        if day["comparable_hours"]:
            group["a_m3"] += day["a_m3"]
            group["b_m3"] += day["b_m3"]
    for group in grouped.values():
        count = group["comparable_hours"]
        group["complete"] = count == group["expected_hours"] == group["selected_hours"]
        if count:
            group["a_m3"] = round(group["a_m3"], 9)
            group["b_m3"] = round(group["b_m3"], 9)
            group["difference_m3"] = round(group["a_m3"] - group["b_m3"], 9)
            group["difference_litres"] = round(group["difference_m3"] * 1000, 6)
        else:
            group["a_m3"] = group["b_m3"] = None
            group["difference_m3"] = group["difference_litres"] = None
    return list(grouped.values())


def compare_hourly_statistics(
    source_a: Iterable[Mapping[str, object]],
    source_b: Iterable[Mapping[str, object]],
    start: datetime,
    end: datetime,
    *,
    as_of: datetime,
    time_zone: str = "Europe/Prague",
) -> dict[str, Any]:
    """Porovnat hodinové statistiky v m³ v intervalu [start, end).

    Časy jsou začátky hodin Recorderu. Přírůstek hodiny je rozdíl sum v daném
    a bezprostředně předchozím bodě; samotný různý počátek sum není rozdíl
    spotřeby. Neúplnou hodinu dle as_of, mezery, duplicity, chybné sumy a
    poklesy označíme jako neporovnatelné. Souhrn používá jen společné platné
    intervaly, den je úplný pouze při skutečném pokrytí celého místního dne.
    Zdrojové body mohou obsahovat pomocné importní kotvy; jejich provenienci
    nelze ze sum spolehlivě určit, proto se neoznačují za skutečné odečty.
    Výsledek je pouze výpočet a nic nikam nezapisuje.
    """
    start_utc, end_utc, now_utc = _utc(start), _utc(end), _utc(as_of)
    if start_utc is None or end_utc is None or now_utc is None:
        raise ValueError("Čas musí mít jednoznačnou časovou zónu")
    if any(x.minute or x.second or x.microsecond for x in (start_utc, end_utc)):
        raise ValueError("Hranice musí být přesné hodiny")
    hours = (end_utc - start_utc).total_seconds() / 3600
    if hours <= 0 or hours > MAX_COMPARE_HOURS:
        raise ValueError("Neplatný nebo příliš dlouhý interval")
    zone = ZoneInfo(time_zone)
    a, anomalies_a = _index(source_a)
    b, anomalies_b = _index(source_b)
    complete_before = now_utc.replace(minute=0, second=0, microsecond=0)
    rows: list[dict[str, Any]] = []
    daily: dict[str, dict[str, Any]] = {}
    current = start_utc
    while current < end_utc:
        local = current.astimezone(zone)
        key = local.date().isoformat()
        day = daily.setdefault(
            key,
            {
                "date": key,
                "expected_hours": _day_hours(local.date(), zone),
                "selected_hours": 0,
                "comparable_hours": 0,
                "a_m3": 0.0,
                "b_m3": 0.0,
            },
        )
        day["selected_hours"] += 1
        da, error_a = _change(a, current)
        db, error_b = _change(b, current)
        if current >= complete_before:
            da, db = None, None
            error_a = error_b = "incomplete_hour"
        comparable = da is not None and db is not None
        difference = round(da - db, 9) if comparable else None
        if comparable:
            day["comparable_hours"] += 1
            day["a_m3"] += da
            day["b_m3"] += db
        rows.append(
            {
                "start_utc": current.isoformat(),
                "start_local": local.isoformat(),
                "state_a_m3": a.get(current, {}).get("state"),
                "state_b_m3": b.get(current, {}).get("state"),
                "sum_a_m3": a.get(current, {}).get("sum"),
                "sum_b_m3": b.get(current, {}).get("sum"),
                "a_m3": da,
                "b_m3": db,
                "difference_m3": difference,
                "difference_litres": round(difference * 1000, 6)
                if difference is not None
                else None,
                "status_a": error_a or "ok",
                "status_b": error_b or "ok",
                "comparable": comparable,
            }
        )
        current += HOUR

    result_days: list[dict[str, Any]] = []
    for day in daily.values():
        count = day["comparable_hours"]
        day["complete"] = count == day["expected_hours"] == day["selected_hours"]
        if count:
            day["a_m3"] = round(day["a_m3"], 9)
            day["b_m3"] = round(day["b_m3"], 9)
            day["difference_m3"] = round(day["a_m3"] - day["b_m3"], 9)
            day["difference_litres"] = round(day["difference_m3"] * 1000, 6)
        else:
            day["a_m3"] = day["b_m3"] = None
            day["difference_m3"] = day["difference_litres"] = None
        result_days.append(day)

    comparable_rows = [row for row in rows if row["comparable"]]
    total_a = (
        round(sum(row["a_m3"] for row in comparable_rows), 9)
        if comparable_rows
        else None
    )
    total_b = (
        round(sum(row["b_m3"] for row in comparable_rows), 9)
        if comparable_rows
        else None
    )
    total_difference = round(total_a - total_b, 9) if comparable_rows else None
    return {
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "as_of_utc": now_utc.isoformat(),
        "time_zone": time_zone,
        "unit": "m³",
        "provenance": "unknown",
        "anomalies_a": anomalies_a,
        "anomalies_b": anomalies_b,
        "requested_hours": len(rows),
        "comparable_hours": len(comparable_rows),
        "a_m3": total_a,
        "b_m3": total_b,
        "difference_m3": total_difference,
        "difference_litres": round(total_difference * 1000, 6)
        if total_difference is not None
        else None,
        "hours": rows,
        "days": result_days,
        "months": _calendar_periods(result_days, zone, yearly=False),
        "years": _calendar_periods(result_days, zone, yearly=True),
    }
