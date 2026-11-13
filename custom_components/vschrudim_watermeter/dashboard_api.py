"""Pouze správcovské čtení registrů a statistik pro generátor dashboardu."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_list_statistic_ids,
    statistics_during_period,
)
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.yaml import dump

from .const import DOMAIN
from .dashboard import PANEL_PATH, build_dashboard
from .dashboard_sources import (
    ENTITY_KEYS,
    convert_to_m3,
    metadata_unit,
    resolve_entities,
    resolve_statistics,
)
from .statistics_compare import compare_hourly_statistics

_LOGGER = logging.getLogger(__name__)
_FRONTEND = Path(__file__).parent / "frontend" / "dashboard-generator.js"
MODULE_URL = "/vschrudim_watermeter/dashboard-generator.js"
PANEL_ELEMENT = "vschrudim-dashboard-generator"
_STATE_KEY = f"{DOMAIN}.dashboard_registration"


async def async_setup_dashboard_generator(hass: HomeAssistant) -> None:
    """Zaregistrovat jeden panel pro všechny položky; souběžný setup čeká.

    Krokování dovoluje bezpečně opakovat registraci, pokud frontend ještě není
    dostupný. Výjimka se zpracuje ve volajícím bez přerušení čtení vodoměru.
    """
    state = hass.data.setdefault(_STATE_KEY, {"lock": asyncio.Lock()})
    async with state["lock"]:
        if "frontend" not in hass.config.components:
            # Při startu může integrace vodoměru předběhnout frontend. Jediný
            # posluchač po jeho načtení registraci dokončí; čtení vody nečeká.
            if "frontend_listener" not in state:

                @callback
                def frontend_loaded(event: Any) -> None:
                    if event.data.get("component") != "frontend":
                        return
                    state.pop("frontend_listener")()
                    hass.async_create_task(_async_retry_panel_setup(hass))

                state["frontend_listener"] = hass.bus.async_listen(
                    EVENT_COMPONENT_LOADED, frontend_loaded
                )
            return
        from homeassistant.components.frontend import async_panel_exists
        from homeassistant.components.http import StaticPathConfig
        from homeassistant.components.panel_custom import async_register_panel

        if not state.get("commands"):
            for handler in (websocket_sources, websocket_preview, websocket_compare):
                websocket_api.async_register_command(hass, handler)
            state["commands"] = True
        if not state.get("static"):
            await hass.http.async_register_static_paths(
                [StaticPathConfig(MODULE_URL, str(_FRONTEND), False)]
            )
            state["static"] = True
        if state.get("panel"):
            return
        if async_panel_exists(hass, PANEL_PATH):
            _LOGGER.warning("Adresa generátoru dashboardu VSChrudim je již obsazená")
            return
        contents = await hass.async_add_executor_job(_FRONTEND.read_bytes)
        version = hashlib.sha256(contents).hexdigest()[:16]
        state["module_url"] = f"{MODULE_URL}?v={version}"
        await async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name=PANEL_ELEMENT,
            module_url=state["module_url"],
            require_admin=True,
        )
        state["panel"] = True


async def _async_retry_panel_setup(hass: HomeAssistant) -> None:
    """Pozdní registrace má stejnou izolaci chyb jako úvodní setup."""
    try:
        await async_setup_dashboard_generator(hass)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Generátor dashboardu není dostupný (%s)", type(err).__name__)


def _entry_sources(hass: HomeAssistant, entry: Any) -> tuple[dict[str, str], str, str]:
    """Provázat načtenou entry s jejím zařízením, registry a ID writeru."""
    coordinator = entry.runtime_data
    identifier = coordinator.place.identifier
    # Identifikátor nemusí být globálně jedinečný napříč ConfigEntry. Proto
    # nejprve omezíme registry na danou entry a nejednoznačnou shodu odmítneme.
    devices = [
        device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if (DOMAIN, identifier) in device.identifiers
    ]
    entities = resolve_entities(
        er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id),
        entry_id=entry.entry_id,
        place_id=identifier,
        device_id=devices[0].id if len(devices) == 1 else "",
    )
    return entities, coordinator.consumption_statistic_id, coordinator.cost_statistic_id


def _loaded_entry(hass: HomeAssistant, entry_id: str) -> Any | None:
    """Vrátit jen skutečně načtenou vlastní položku s inicializovanými daty."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    try:
        coordinator = entry.runtime_data
    except AttributeError:
        return None
    return entry if coordinator is not None else None


@websocket_api.require_admin
@websocket_api.websocket_command({"type": f"{DOMAIN}/dashboard/sources"})
@websocket_api.async_response
async def websocket_sources(
    hass: HomeAssistant, connection: Any, msg: dict[str, Any]
) -> None:
    """Předat správci seznam míst a kandidátů bez přístupových údajů."""
    metadata = await async_list_statistic_ids(hass, statistic_type="sum")
    entries = []
    all_candidates: list[dict[str, str]] = []
    for raw_entry in hass.config_entries.async_entries(DOMAIN):
        entry = _loaded_entry(hass, raw_entry.entry_id)
        if entry is None:
            continue
        entities, own_id, cost_id = _entry_sources(hass, entry)
        own, candidates = resolve_statistics(
            metadata, own_consumption_id=own_id, own_cost_id=cost_id
        )
        entries.append(
            {
                "entry_id": entry.entry_id,
                "name": entry.title,
                "entities": entities,
                "statistics": own,
            }
        )
        all_candidates = candidates
    if not entries:
        _, all_candidates = resolve_statistics(
            metadata, own_consumption_id="", own_cost_id=""
        )
    connection.send_result(
        msg["id"],
        {
            "entries": entries,
            "candidates": all_candidates,
            "comparison_integration_states": [
                getattr(entry.state, "value", str(entry.state))
                for entry in hass.config_entries.async_entries("vschrudim")
            ],
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/dashboard/preview",
        vol.Required("entry_id"): str,
        vol.Required("title"): vol.All(str, vol.Length(min=1, max=100)),
        vol.Required("url_path"): vol.All(str, vol.Length(min=3, max=60)),
        vol.Required("language"): vol.In(("cs", "en")),
        vol.Optional("target", default="overview"): vol.In(("overview",)),
        vol.Optional("comparison"): str,
        vol.Optional("same_meter", default=False): bool,
    }
)
@websocket_api.async_response
async def websocket_preview(
    hass: HomeAssistant, connection: Any, msg: dict[str, Any]
) -> None:
    """Sestavit celý dashboard z aktuálních registrů, bez volání portálu."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(
            msg["id"], "entry_not_loaded", "Integration entry is not loaded"
        )
        return
    entities, own_id, cost_id = _entry_sources(hass, entry)
    metadata = await async_list_statistic_ids(hass, statistic_type="sum")
    own, candidates = resolve_statistics(
        metadata, own_consumption_id=own_id, own_cost_id=cost_id
    )
    comparison = msg.get("comparison")
    valid = {candidate["statistic_id"]: candidate["unit"] for candidate in candidates}
    if comparison and (
        not msg["same_meter"] or comparison not in valid or "consumption" not in own
    ):
        connection.send_error(
            msg["id"],
            "invalid_comparison",
            "Select and confirm an existing meter statistic",
        )
        return
    try:
        config = await hass.async_add_executor_job(
            partial(
                build_dashboard,
                title=msg["title"],
                url_path=msg["url_path"],
                language=msg["language"],
                entities=entities,
                statistics=own,
                comparison=comparison,
                comparison_unit=valid.get(comparison),
                target=msg["target"],
            )
        )
        yaml = await hass.async_add_executor_job(dump, config)
    except ValueError as err:
        connection.send_error(msg["id"], str(err), "Invalid dashboard configuration")
        return
    connection.send_result(
        msg["id"],
        {
            "config": config,
            "yaml": yaml,
            "missing_entities": [key for key in ENTITY_KEYS if key not in entities],
            "missing_statistics": [
                key for key in ("consumption", "cost") if key not in own
            ],
            "comparison": bool(comparison),
            "resource_url": hass.data.get(_STATE_KEY, {}).get("module_url")
            if comparison
            else None,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/dashboard/compare",
        vol.Required("source_a"): str,
        vol.Required("source_b"): str,
        vol.Required("start"): int,
        vol.Required("end"): int,
    }
)
@websocket_api.async_response
async def websocket_compare(
    hass: HomeAssistant, connection: Any, msg: dict[str, Any]
) -> None:
    """Přečíst omezený rozsah Recorderu; žádné přihlášení ani zápis.

    Vstupem jsou sekundy UTC; jednotky m³/l se ověřují v metadatech. Dotaz
    zahrne jednu předchozí hodinu a výpočet běží mimo event loop. Endpoint
    nepovoluje libovolná cizí ID ani více než 366 dní v jediném požadavku.
    """
    try:
        start = datetime.fromtimestamp(msg["start"], UTC)
        end = datetime.fromtimestamp(msg["end"], UTC)
        if start >= end or end - start > timedelta(days=366):
            raise ValueError("invalid_period")
    except (ValueError, OverflowError, OSError):
        connection.send_error(msg["id"], "invalid_period", "Invalid comparison period")
        return
    metadata = await async_list_statistic_ids(hass, statistic_type="sum")
    owned = set()
    for raw_entry in hass.config_entries.async_entries(DOMAIN):
        entry = _loaded_entry(hass, raw_entry.entry_id)
        if entry:
            owned.add(entry.runtime_data.consumption_statistic_id)
    a_meta = next(
        (
            m
            for m in metadata
            if m.get("statistic_id") == msg["source_a"]
            and m.get("source") == DOMAIN
            and m.get("has_sum")
        ),
        None,
    )
    b_meta = next(
        (
            m
            for m in metadata
            if m.get("statistic_id") == msg["source_b"]
            and m.get("source") == "vschrudim"
            and m.get("has_sum")
        ),
        None,
    )
    if msg["source_a"] not in owned or not a_meta or not b_meta:
        connection.send_error(
            msg["id"], "invalid_sources", "Statistics are not available"
        )
        return
    unit_a, unit_b = metadata_unit(a_meta), metadata_unit(b_meta)
    if not unit_a or not unit_b:
        connection.send_error(
            msg["id"], "invalid_units", "Unsupported water statistic unit"
        )
        return
    try:
        rows = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start - timedelta(hours=1),
            end,
            {msg["source_a"], msg["source_b"]},
            "hour",
            None,
            {"state", "sum"},
        )
        result = await hass.async_add_executor_job(
            partial(
                compare_hourly_statistics,
                convert_to_m3(rows.get(msg["source_a"], []), unit_a),
                convert_to_m3(rows.get(msg["source_b"], []), unit_b),
                start,
                end,
                as_of=datetime.now(UTC),
                time_zone="Europe/Prague",
            )
        )
    except (ValueError, OverflowError):
        connection.send_error(
            msg["id"], "invalid_period", "Invalid comparison period or timezone"
        )
        return
    connection.send_result(msg["id"], result)
