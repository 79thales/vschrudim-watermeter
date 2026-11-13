"""Test správcovských websocket příkazů a skutečného testovacího Recorderu.

Všechny hodnoty, účty a místa jsou smyšlené. Síť vede jen do lokálního
testovacího HA, nikoli do portálu nebo k uživateli.
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
import yaml
from homeassistant.components import frontend, websocket_api
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vschrudim_watermeter.const import DOMAIN
from custom_components.vschrudim_watermeter.dashboard import PANEL_PATH
from custom_components.vschrudim_watermeter.dashboard_api import (
    async_setup_dashboard_generator,
    websocket_compare,
    websocket_preview,
    websocket_sources,
)
from custom_components.vschrudim_watermeter.models import ConsumptionPlace

PLACE = ConsumptionPlace("place-a", "technical-a", "Smyšlené místo", "", "")
COMPONENT = "custom_components.vschrudim_watermeter.dashboard_api"


@pytest.fixture
def dashboard_entry(hass):
    """Registrovat jeden ověřitelný příklad s uživatelem přejmenovanou entitou."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Smyšlené místo",
        data={
            "username": "never-export-user",
            "password": "never-export-password",
            "place": asdict(PLACE),
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        place=PLACE,
        consumption_statistic_id=f"{DOMAIN}:{entry.entry_id.casefold()}_water_consumption",
        cost_statistic_id=f"{DOMAIN}:{entry.entry_id.casefold()}_water_cost",
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, PLACE.identifier)}
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "place-a_meter_state",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="prejmenovany_vodomer",
    )
    return entry


async def command(client, message):
    """Odeslat jeden příkaz přes skutečný websocket testovacího HA."""
    await client.send_json_auto_id(message)
    return await client.receive_json()


async def test_preview_is_admin_only_and_never_fetches_portal(
    hass, hass_ws_client, dashboard_entry
):
    """Náhled používá registry a metadata, nikoliv API klienta či údržbu."""
    client = await hass_ws_client()
    websocket_api.async_register_command(hass, websocket_preview)
    metadata = [
        {
            "source": DOMAIN,
            "statistic_id": dashboard_entry.runtime_data.consumption_statistic_id,
            "has_sum": True,
            "statistics_unit_of_measurement": "m³",
        },
        {
            "source": "vschrudim",
            "statistic_id": "vschrudim:synthetic",
            "has_sum": True,
            "statistics_unit_of_measurement": "m³",
        },
    ]
    with patch(
        f"{COMPONENT}.async_list_statistic_ids",
        new_callable=AsyncMock,
        return_value=metadata,
    ):
        result = await command(
            client,
            {
                "type": f"{DOMAIN}/dashboard/preview",
                "entry_id": dashboard_entry.entry_id,
                "title": "Voda",
                "url_path": "voda-test",
                "language": "cs",
                "comparison": "vschrudim:synthetic",
                "same_meter": True,
            },
        )
    assert result["success"]
    output = json.dumps(result, ensure_ascii=False)
    assert "sensor.prejmenovany_vodomer" in output
    assert "never-export-password" not in output
    assert "never-export-user" not in output
    assert "vschrudim:synthetic" in output
    assert len(result["result"]["config"]["views"]) == 2
    assert yaml.safe_load(result["result"]["yaml"]) == result["result"]["config"]


async def test_read_only_user_and_missing_identity_are_rejected(
    hass, hass_ws_client, hass_read_only_access_token, dashboard_entry
):
    """Oprávnění vynucuje HA server, nejen stav tlačítka v prohlížeči."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    websocket_api.async_register_command(hass, websocket_sources)
    result = await command(client, {"type": f"{DOMAIN}/dashboard/sources"})
    assert not result["success"]
    assert result["error"]["code"] == "unauthorized"
    with pytest.raises(Unauthorized):
        websocket_sources(hass, SimpleNamespace(user=None), {"id": 1})


async def test_preview_requires_explicit_meter_confirmation(
    hass, hass_ws_client, dashboard_entry
):
    client = await hass_ws_client()
    websocket_api.async_register_command(hass, websocket_preview)
    metadata = [
        {
            "source": DOMAIN,
            "statistic_id": dashboard_entry.runtime_data.consumption_statistic_id,
            "has_sum": True,
            "statistics_unit_of_measurement": "m³",
        },
        {
            "source": "vschrudim",
            "statistic_id": "vschrudim:synthetic",
            "has_sum": True,
            "statistics_unit_of_measurement": "m³",
        },
    ]
    with patch(
        f"{COMPONENT}.async_list_statistic_ids",
        new_callable=AsyncMock,
        return_value=metadata,
    ):
        response = await command(
            client,
            {
                "type": f"{DOMAIN}/dashboard/preview",
                "entry_id": dashboard_entry.entry_id,
                "title": "Voda",
                "url_path": "voda-test",
                "language": "cs",
                "comparison": "vschrudim:synthetic",
                "same_meter": False,
            },
        )
    assert not response["success"]
    assert response["error"]["code"] == "invalid_comparison"


async def test_comparison_reads_real_recorder_hourly_points(
    hass, hass_ws_client, dashboard_entry, recorder_mock
):
    """Zapsat jen do testovací databáze a číst dvě skutečné hodinové řady."""
    client = await hass_ws_client()
    websocket_api.async_register_command(hass, websocket_compare)
    first = datetime(2026, 1, 1, tzinfo=UTC)
    source_a = dashboard_entry.runtime_data.consumption_statistic_id
    source_b = "vschrudim:synthetic"
    for source, statistic_id, values in (
        (DOMAIN, source_a, [100, 100.125, 100.15]),
        ("vschrudim", source_b, [200, 200.125, 200.15]),
    ):
        metadata = {
            "source": source,
            "statistic_id": statistic_id,
            "name": "Smyšlená statistika",
            "unit_of_measurement": "m³",
            "unit_class": "volume",
            "has_sum": True,
            "has_mean": False,
            "mean_type": StatisticMeanType.NONE,
        }
        rows = [
            {"start": first + timedelta(hours=index), "state": value, "sum": value}
            for index, value in enumerate(values)
        ]
        async_add_external_statistics(hass, metadata, rows)
    await get_instance(hass).async_block_till_done()
    response = await command(
        client,
        {
            "type": f"{DOMAIN}/dashboard/compare",
            "source_a": source_a,
            "source_b": source_b,
            "start": int((first + timedelta(hours=1)).timestamp()),
            "end": int((first + timedelta(hours=3)).timestamp()),
        },
    )
    assert response["success"], response
    result = response["result"]
    assert result["comparable_hours"] == 2
    assert result["a_m3"] == pytest.approx(0.15)
    assert result["difference_litres"] == pytest.approx(0)
    assert source_a not in json.dumps(result)
    assert source_b not in json.dumps(result)


async def test_real_recorder_missing_hour_and_late_recovery_on_dst_day(
    hass, hass_ws_client, dashboard_entry, recorder_mock
):
    """Skutečná testovací DB: mezera není nula, pozdější bod obnoví pokrytí."""
    client = await hass_ws_client()
    websocket_api.async_register_command(hass, websocket_compare)
    prague = ZoneInfo("Europe/Prague")
    start = datetime(2026, 3, 29, tzinfo=prague).astimezone(UTC)
    end = datetime(2026, 3, 30, tzinfo=prague).astimezone(UTC)
    source_a = dashboard_entry.runtime_data.consumption_statistic_id
    source_b = "vschrudim:synthetic"
    count = int((end - start).total_seconds() // 3600)
    assert count == 23
    metadata_by_id = {}
    samples_by_id = {}
    for source, statistic_id, base in (
        (DOMAIN, source_a, 100),
        ("vschrudim", source_b, 200),
    ):
        metadata = {
            "source": source,
            "statistic_id": statistic_id,
            "name": "Smyšlená statistika",
            "unit_of_measurement": "m³",
            "unit_class": "volume",
            "has_sum": True,
            "has_mean": False,
            "mean_type": StatisticMeanType.NONE,
        }
        rows = [
            {
                "start": start + timedelta(hours=index - 1),
                "state": base + index / 1000,
                "sum": base + index / 1000,
            }
            for index in range(count + 1)
        ]
        metadata_by_id[statistic_id], samples_by_id[statistic_id] = metadata, rows
        async_add_external_statistics(
            hass, metadata, rows if statistic_id == source_a else rows[:6] + rows[7:]
        )
    await get_instance(hass).async_block_till_done()
    request = {
        "type": f"{DOMAIN}/dashboard/compare",
        "source_a": source_a,
        "source_b": source_b,
        "start": int(start.timestamp()),
        "end": int(end.timestamp()),
    }
    before = await command(client, dict(request))
    assert before["success"]
    assert before["result"]["days"][0]["expected_hours"] == 23
    assert before["result"]["comparable_hours"] == 21
    assert not before["result"]["days"][0]["complete"]
    assert any(row["status_b"] == "missing" for row in before["result"]["hours"])
    async_add_external_statistics(
        hass, metadata_by_id[source_b], [samples_by_id[source_b][6]]
    )
    await get_instance(hass).async_block_till_done()
    after = await command(client, dict(request))
    assert after["success"]
    assert after["result"]["comparable_hours"] == 23
    assert after["result"]["days"][0]["complete"]
    assert after["result"]["difference_litres"] == pytest.approx(0)


async def test_late_frontend_registers_one_hidden_admin_panel(hass, hass_ws_client):
    """Později načtený frontend nesmí zablokovat integraci ani vytvořit sidebar."""
    await hass_ws_client()
    await async_setup_dashboard_generator(hass)
    state = hass.data[f"{DOMAIN}.dashboard_registration"]
    assert not state.get("panel")
    assert "frontend_listener" in state
    assert await async_setup_component(hass, "frontend", {})
    await hass.async_block_till_done()
    assert state.get("panel")
    assert "frontend_listener" not in state
    panel = hass.data[frontend.DATA_PANELS][PANEL_PATH]
    assert panel.sidebar_title is None
    assert panel.require_admin is True
    assert panel.config["_panel_custom"]["module_url"].startswith(
        "/vschrudim_watermeter/dashboard-generator.js?v="
    )
    await async_setup_dashboard_generator(hass)
    assert hass.data[frontend.DATA_PANELS][PANEL_PATH] is panel


async def test_options_link_does_not_change_existing_configuration(
    hass, dashboard_entry
):
    """Otevření a zavření samostatného odkazu nepřepíše data ani options."""
    old_data, old_options = dict(dashboard_entry.data), dict(dashboard_entry.options)
    result = await hass.config_entries.options.async_init(dashboard_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["url"].startswith("/" + PANEL_PATH)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "dashboard_closed"
    assert dict(dashboard_entry.data) == old_data
    assert dict(dashboard_entry.options) == old_options
