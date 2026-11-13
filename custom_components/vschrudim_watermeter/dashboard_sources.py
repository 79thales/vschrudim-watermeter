"""Čisté filtrování zdrojů dashboardu podle registrů a metadat Recorderu."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

ENTITY_KEYS = (
    "meter_state",
    "latest_consumption",
    "water_price",
    "total_water_cost",
    "source_status",
    "data_available_through",
    "latest_portal_reading",
    "missing_hourly_readings",
    "oldest_missing_hourly_reading",
    "last_download_reading_count",
    "duplicate_readings_merged",
    "missing_readings_recovered",
    "last_update_attempt",
    "history_backfill_status",
    "energy_statistics_status",
    "meter_register_status",
    "test_download",
    "check_energy_statistics",
)


def resolve_entities(
    registry_entries: Iterable[Any], *, entry_id: str, place_id: str, device_id: str
) -> dict[str, str]:
    """Vybrat pouze aktivní entity daného zařízení, entry a přesného unique ID.

    Entity ID se čte z registru, takže přejmenování uživatelem zůstane platné.
    Vypnutá entita ani entita jiného odběrného místa se nesmí zaměnit za nulu.
    """
    expected = {f"{place_id}_{key}": key for key in ENTITY_KEYS}
    result: dict[str, str] = {}
    for item in registry_entries:
        if (
            item.platform != "vschrudim_watermeter"
            or item.config_entry_id != entry_id
            or item.device_id != device_id
            or item.disabled_by is not None
        ):
            continue
        key = expected.get(item.unique_id)
        if key is not None:
            result[key] = item.entity_id
    return result


def metadata_unit(meta: Mapping[str, Any]) -> str | None:
    """Vrátit pouze známou jednotku objemu uvedenou v metadatech databáze."""
    value = meta.get("statistics_unit_of_measurement")
    if value is None:
        value = meta.get("unit_of_measurement")
    return value if value in ("m³", "L", "l") else None


def resolve_statistics(
    metadata: Iterable[Mapping[str, Any]],
    *,
    own_consumption_id: str,
    own_cost_id: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Ověřit existenci vlastních ID a nabídnout pouze skutečné součty rovocz.

    Metadata nepředstavují důkaz totožnosti vodoměru. Cizí kandidáty jsou
    nevybrané; jejich přiřazení musí uživatel výslovně odsouhlasit.
    """
    own: dict[str, str] = {}
    candidates: list[dict[str, str]] = []
    for meta in metadata:
        identifier = meta.get("statistic_id")
        if not isinstance(identifier, str) or not meta.get("has_sum"):
            continue
        source = meta.get("source")
        if source == "vschrudim_watermeter":
            if identifier == own_consumption_id and metadata_unit(meta) == "m³":
                own["consumption"] = identifier
            elif identifier == own_cost_id:
                own["cost"] = identifier
        elif source == "vschrudim" and (unit := metadata_unit(meta)):
            candidates.append(
                {
                    "statistic_id": identifier,
                    "name": str(meta.get("name") or identifier),
                    "unit": unit,
                }
            )
    return own, sorted(
        candidates, key=lambda item: (item["name"], item["statistic_id"])
    )


def convert_to_m3(rows: Iterable[Mapping[str, Any]], unit: str) -> list[dict[str, Any]]:
    """Výslovně převést pouze litr na m³; nečíselné hodnoty zůstanou neplatné."""
    if unit not in ("m³", "L", "l"):
        raise ValueError("unsupported_unit")
    factor = 0.001 if unit in ("L", "l") else 1.0
    converted = []
    for source in rows:
        row = dict(source)
        for key in ("sum", "state"):
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row[key] = value * factor
        converted.append(row)
    return converted
