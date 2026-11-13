"""Sestavení přehledu z existujících vazeb bez změny stavu Home Assistantu."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

PANEL_PATH = "vschrudim-watermeter-generator"
_TEMPLATE = Path(__file__).with_name("dashboard_template.json")
_PATH = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+\Z")
_ENTITY = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+\Z")
_STATISTIC = re.compile(r"[a-z0-9_]+(?::|\.)[a-z0-9_]+\Z")


def _text(language: str, cs: str, en: str) -> str:
    return cs if language == "cs" else en


def build_dashboard(
    *,
    title: str,
    url_path: str,
    language: str,
    entities: dict[str, str],
    statistics: dict[str, str],
    comparison: str | None = None,
    comparison_unit: str | None = None,
    target: str = "overview",
) -> dict[str, Any]:
    """Vrátit konfiguraci dashboardu; entity a statistiky dodává výhradně registr.

    Šablona určuje složení hlavního i detailního pohledu. Chybějící entity
    a neexistující statistiky se vynechají, nevyrobí se jejich ID ani nuly.
    Porovnání je zapnuto pouze po výběru skutečného ID druhé statistiky.
    Tato funkce nemá žádný síťový přístup a nic v HA neukládá.
    """
    if not title.strip() or len(title) > 100 or any(ord(ch) < 32 for ch in title):
        raise ValueError("invalid_title")
    if not _PATH.fullmatch(url_path) or len(url_path) > 60:
        raise ValueError("invalid_path")
    if language not in ("cs", "en"):
        raise ValueError("invalid_language")
    if target != "overview":
        raise ValueError("invalid_target")
    if any(not _ENTITY.fullmatch(item) for item in entities.values()):
        raise ValueError("invalid_entity")
    if any(not _STATISTIC.fullmatch(item) for item in statistics.values()):
        raise ValueError("invalid_statistic")
    if comparison is not None and not _STATISTIC.fullmatch(comparison):
        raise ValueError("invalid_statistic")
    if comparison and comparison_unit not in ("m³", "L", "l"):
        raise ValueError("invalid_unit")

    # Jediný zdroj rozložení se používá i pro YAML, náhled a zápis do Lovelace.
    dashboard = deepcopy(json.loads(_TEMPLATE.read_text(encoding="utf-8")))
    main, detail = dashboard["views"]
    main["title"], main["path"] = title.strip(), url_path
    detail["title"] = _text(language, "Podrobnosti o vodě", "Water details")
    detail["path"] = f"{url_path}-detail"
    left, right = (section["cards"] for section in main["sections"])
    detail_left, detail_right = (section["cards"] for section in detail["sections"])

    def entity_card(key: str, label_cs: str, label_en: str) -> None:
        if entity := entities.get(key):
            left.append(
                {
                    "type": "tile",
                    "entity": entity,
                    "name": _text(language, label_cs, label_en),
                }
            )

    entity_card("meter_state", "Stav vodoměru", "Meter reading")
    entity_card(
        "latest_portal_reading", "Poslední odečet na portálu", "Latest portal reading"
    )
    entity_card(
        "latest_consumption", "Poslední rozdíl odečtů", "Latest reading difference"
    )
    entity_card("water_price", "Aktuální cena vody", "Current water price")
    entity_card("total_water_cost", "Stavové náklady", "Current meter cost")
    entity_card("source_status", "Stav zdroje", "Source status")
    if entity := entities.get("last_update_attempt"):
        # Atribut je čas úspěchu, stav entity je čas posledního pokusu.
        left.append(
            {
                "type": "entities",
                "title": _text(language, "Načítání", "Downloads"),
                "entities": [
                    {
                        "entity": entity,
                        "name": _text(language, "Poslední pokus", "Last attempt"),
                    },
                    {
                        "type": "attribute",
                        "entity": entity,
                        "attribute": "last_success",
                        "name": _text(
                            language,
                            "Poslední úspěšné stažení",
                            "Last successful download",
                        ),
                    },
                ],
            }
        )
    left.append(
        {
            "type": "markdown",
            "content": _text(
                language,
                "Poslední spotřeba je rozdíl odečtů, nikoli okamžitý průtok. "
                "Portál může zveřejňovat údaje se zpožděním.",
                "Latest consumption is a difference between readings, not an instantaneous flow. "
                "Portal data may be published with a delay.",
            ),
        }
    )
    for key, cs_name, en_name in (
        ("consumption", "Spotřeba", "Consumption"),
        ("cost", "Náklady", "Cost"),
    ):
        if statistic := statistics.get(key):
            for period, cs_period, en_period in (
                ("day", "dnes", "today"),
                ("month", "tento měsíc", "this month"),
                ("year", "letos", "this year"),
            ):
                right.append(
                    {
                        "type": "statistic",
                        "entity": statistic,
                        "name": f"{_text(language, cs_name, en_name)} – {_text(language, cs_period, en_period)}",
                        "stat_type": "change",
                        "period": {"calendar": {"period": period}},
                    }
                )
    right.append(
        {
            "type": "markdown",
            "content": _text(
                language,
                "Součty vycházejí z dostupných statistik HA. Aktuální období a "
                "období s mezerami mohou být neúplná; stáří portálu není chyba.",
                "Totals use available HA statistics. Current periods and periods "
                "with gaps can be incomplete; portal data age is not a failure.",
            ),
        }
    )
    if consumption := statistics.get("consumption"):
        graph_sources: list[str] = [consumption]
        if comparison and comparison_unit == "m³":
            graph_sources.append(comparison)
        elif comparison:
            detail_left.append(
                {
                    "type": "markdown",
                    "content": _text(
                        language,
                        "Cizí statistika je v litrech: přísné porovnání ji převádí "
                        "na m³. Nativní graf uvádí pouze vlastní zdroj, protože "
                        "nelze zaručit společnou jednotku osy.",
                        "The other statistic is in litres: strict comparison converts "
                        "it to m³. The native chart shows only the local source because "
                        "a common axis unit cannot be guaranteed.",
                    ),
                }
            )
        for period, days, cs_name, en_name in (
            ("hour", 3, "Hodinová spotřeba", "Hourly consumption"),
            ("day", 31, "Denní spotřeba", "Daily consumption"),
            ("month", 365, "Měsíční spotřeba", "Monthly consumption"),
        ):
            # Jedna společná osa pro oba zdroje zabraňuje optickému zkreslení.
            detail_left.append(
                {
                    "type": "statistics-graph",
                    "title": _text(language, cs_name, en_name),
                    "entities": graph_sources,
                    "chart_type": "bar",
                    "stat_types": ["change"],
                    "period": period,
                    "days_to_show": days,
                }
            )
    diagnostic = [
        entities[key]
        for key in (
            "history_backfill_status",
            "energy_statistics_status",
        "data_available_through",
        "last_download_reading_count",
        "duplicate_readings_merged",
        "missing_readings_recovered",
            "missing_hourly_readings",
            "oldest_missing_hourly_reading",
            "meter_register_status",
        )
        if key in entities
    ]
    if diagnostic:
        detail_right.append(
            {
                "type": "entities",
                "title": _text(language, "Diagnostika", "Diagnostics"),
                "entities": diagnostic,
            }
        )
    for key, cs_name, en_name in (
        ("test_download", "Otestovat stažení", "Test download"),
        ("check_energy_statistics", "Zkontrolovat statistiky", "Check statistics"),
    ):
        if button := entities.get(key):
            detail_right.append(
                {
                    "type": "button",
                    "entity": button,
                    "name": _text(language, cs_name, en_name),
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": "button.press",
                        "target": {"entity_id": button},
                    },
                }
            )
    if comparison and consumption:
        detail_right.append(
            {
                "type": "custom:vschrudim-statistics-comparison",
                "source_a": consumption,
                "source_b": comparison,
                "language": language,
            }
        )
    left.append(
        {
            "type": "button",
            "name": _text(
                language, "Historie a diagnostika", "History and diagnostics"
            ),
            "icon": "mdi:chart-bar",
            "tap_action": {
                "action": "navigate",
                "navigation_path": f"/lovelace/{detail['path']}",
            },
        }
    )
    return dashboard
