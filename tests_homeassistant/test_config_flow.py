"""Regrese instalace a reauth přes skutečnou správu toků Home Assistantu.

Přihlašovací údaje i místa jsou smyšlené. Mockujeme pouze hranici portálu
a v čistých testech toku setup/reload; logika ConfigFlow ani pomocné metody
HA se nenahrazují. Zvláštní test spouští skutečný setup, reload a registry.
"""

import json
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_dumps
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vschrudim_watermeter.api import (
    VsChrudimAuthError,
    VsChrudimConnectionError,
    VsChrudimProtocolError,
)
from custom_components.vschrudim_watermeter.const import DOMAIN
from custom_components.vschrudim_watermeter.models import (
    ConsumptionPlace,
    MeterReading,
    WaterMeterData,
)

COMPONENT = "custom_components.vschrudim_watermeter"
PLACE = ConsumptionPlace("place-a", "technical-a", "Testovací místo A", "a", "1")
OTHER_PLACE = ConsumptionPlace("place-b", "technical-b", "Testovací místo B", "b", "1")
CREDENTIALS = {"username": "TestUser", "password": "synthetic-new-password"}


def make_entry(place=PLACE, **kwargs):
    """Vytvořit původní záznam včetně uživatelských voleb a cizího klíče.

    Cizí klíč chrání před nechtěným nahrazením celého ConfigEntry.data.
    ID účtu odpovídá dosavadnímu casefold algoritmu, nikoli novému formátu.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Vlastní název uživatele",
        unique_id=f"testuser_{place.identifier}",
        version=1,
        minor_version=1,
        data={
            "username": "TestUser",
            "password": "synthetic-old-password",
            "place": asdict(place),
            "retained_data": {"checkpoint": 17},
        },
        options={
            "price_per_m3": 137.5,
            "scan_interval": 90,
            "notify_unavailable": False,
            "failure_threshold": 4,
            "notify_missing": False,
            "missing_retry_attempts": 1,
            "retry_delay": 45,
            "source_delay_warning_hours": 12,
        },
        **kwargs,
    )


@pytest.fixture
def entry(hass):
    """Přidat skutečný ConfigEntry do testovacího správce HA."""
    value = make_entry()
    value.add_to_hass(hass)
    return value


@pytest.fixture
def portal():
    """Simulovat odpovědi portálu bez skutečného HTTP nebo tajných údajů."""
    with patch(
        f"{COMPONENT}.config_flow.VsChrudimClient.async_get_places",
        new_callable=AsyncMock,
        return_value=[PLACE, OTHER_PLACE],
    ) as mock:
        yield mock


@pytest.fixture
def setup_entry():
    """V testech samotného toku oddělit následný setup platformy."""
    with patch(f"{COMPONENT}.async_setup_entry", return_value=True) as mock:
        yield mock


@pytest.fixture
def reload_entry(hass):
    """Zachytit naplánovaný reload; jeho reálný průběh ověřuje test registrů."""
    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock:
        yield mock


async def start_reauth(hass, entry):
    """Zahájit tok přes standardní SOURCE_REAUTH a entry_id v kontextu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def authenticate(hass, result, credentials=None):
    """Odeslat přihlášení přes správce toku a pokračovat k výběru místa."""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], credentials or CREDENTIALS
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "place"
    return result


async def select_place(hass, result, place=PLACE):
    """Dokončit výběr a vyčkat také na naplánované setup/reload úlohy."""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"place": place.identifier}
    )
    await hass.async_block_till_done()
    return result


async def test_first_installation(hass, portal, setup_entry):
    """První instalace založí právě jeden záznam se správnou identitou."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await select_place(hass, await authenticate(hass, result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].unique_id == "testuser_place-a"
    assert entries[0].data == {**CREDENTIALS, "place": asdict(PLACE)}
    assert entries[0].options == {"price_per_m3": 0.0}
    setup_entry.assert_awaited_once()


async def test_duplicate_installation(hass, entry, portal, setup_entry, reload_entry):
    """Opakované přidání místa nemění existující přihlášení ani volby."""
    before = deepcopy(dict(entry.data))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=CREDENTIALS
    )
    result = await select_place(hass, result)
    assert result["reason"] == "already_configured"
    assert entry.data == before
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    setup_entry.assert_not_called()
    reload_entry.assert_not_called()


async def test_add_another_place(hass, entry, portal, setup_entry):
    """Další místo stejného účtu je nadále samostatná podporovaná instalace."""
    before = deepcopy(dict(entry.data))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=CREDENTIALS
    )
    result = await select_place(hass, result, OTHER_PLACE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "testuser_place-b"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    assert entry.data == before


@pytest.mark.parametrize("username", ["TestUser", "TESTUSER", "testuser"])
async def test_reauth_preserves_entry_and_options(hass, entry, portal, reload_entry, username):
    """Nové heslo aktualizuje správný záznam, ostatní data/volby zůstávají."""
    before = deepcopy(dict(entry.data))
    options = deepcopy(dict(entry.options))
    identity = (entry.entry_id, entry.unique_id, entry.title)
    other = make_entry(OTHER_PLACE)
    other.add_to_hass(hass)
    other_before = deepcopy(dict(other.data))
    credentials = {**CREDENTIALS, "username": username}
    result = await authenticate(hass, await start_reauth(hass, entry), credentials)
    result = await select_place(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {**before, **credentials}
    assert entry.options == options
    assert (entry.entry_id, entry.unique_id, entry.title) == identity
    assert other.data == other_before
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (VsChrudimAuthError("synthetic"), "invalid_auth"),
        (VsChrudimConnectionError("synthetic timeout"), "cannot_connect"),
        (VsChrudimProtocolError("synthetic"), "unknown"),
    ],
)
async def test_reauth_error_can_be_retried(hass, entry, portal, reload_entry, error, reason):
    """Chybná odpověď portálu nemění data; tentýž formulář dovolí opakování."""
    before = deepcopy(dict(entry.data))
    options = dict(entry.options)
    portal.side_effect = error
    result = await start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": reason}
    assert entry.data == before
    assert entry.options == options
    reload_entry.assert_not_called()

    portal.side_effect = None
    result = await select_place(hass, await authenticate(hass, result))
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == CREDENTIALS["password"]
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize("different_account", [False, True])
async def test_reauth_rejects_different_identity(
    hass, entry, portal, reload_entry, different_account
):
    """Jiné místo i jiný účet musí skončit bez přepsání kteréhokoli záznamu."""
    other = make_entry(OTHER_PLACE)
    other.add_to_hass(hass)
    before = [(dict(item.data), dict(item.options), item.unique_id) for item in (entry, other)]
    credentials = {**CREDENTIALS, "username": "DifferentUser"} if different_account else None
    result = await authenticate(hass, await start_reauth(hass, entry), credentials)
    result = await select_place(hass, result, PLACE if different_account else OTHER_PLACE)
    assert result["reason"] == "unique_id_mismatch"
    assert [(dict(item.data), dict(item.options), item.unique_id) for item in (entry, other)] == before
    reload_entry.assert_not_called()


@pytest.mark.parametrize("places, reason", [([], "no_places"), ([OTHER_PLACE], "reauth_place_not_found")])
async def test_reauth_missing_original_place(hass, entry, portal, reload_entry, places, reason):
    """Prázdný seznam ani odstraněné původní místo nepřepnou konfiguraci."""
    before = dict(entry.data)
    portal.return_value = places
    result = await start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
    assert entry.data == before
    reload_entry.assert_not_called()


async def test_reauth_after_restoring_config_entries(hass, hass_storage, portal):
    """Restart modelujeme načtením serializovaného záznamu novým správcem.

    Nevkládáme původní objekt do nového správce a neobnovujeme rozpracovaný
    formulář. Cílové entry_id a options musí pocházet z úložiště HA.
    """
    original = make_entry()
    hass_storage["core.config_entries"] = {
        "version": config_entries.STORAGE_VERSION,
        "minor_version": config_entries.STORAGE_VERSION_MINOR,
        "key": "core.config_entries",
        "data": json.loads(json_dumps({"entries": [original.as_storage_fragment]})),
    }
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    restored = hass.config_entries.async_get_entry(original.entry_id)
    assert restored is not original
    assert restored.data == original.data
    with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
        result = await select_place(hass, await authenticate(hass, await start_reauth(hass, restored)))
        assert result["reason"] == "reauth_successful"
        assert restored.unique_id == original.unique_id
        assert restored.options == original.options
        reload.assert_awaited_once_with(original.entry_id)


async def test_portal_auth_failure_starts_standard_reauth(hass, entry, portal):
    """Skutečný koordinátor převede chybu portálu na reauth stejného záznamu.

    První setup narazí na odmítnuté uložené heslo. HA musí sám otevřít tok;
    po opravě hesla proběhne skutečný reload. Při druhém setupu mockujeme
    měření, aby tento test neprováděl nesouvisející import statistik.
    """
    with patch(
        f"{COMPONENT}.api.VsChrudimClient.async_get_data",
        side_effect=VsChrudimAuthError("synthetic expired password"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == entry.entry_id
    data = WaterMeterData(PLACE, (MeterReading(datetime(2026, 10, 19, 10), 100.0),), 0.0)
    with (
        patch(f"{COMPONENT}.coordinator.VsChrudimCoordinator._async_update_data", return_value=data),
        patch(f"{COMPONENT}.coordinator.VsChrudimCoordinator.async_start_history_backfill", return_value=False),
    ):
        result = await select_place(hass, await authenticate(hass, flows[0]))
        assert result["reason"] == "reauth_successful"
        assert entry.state is ConfigEntryState.LOADED
        assert entry.data["password"] == CREDENTIALS["password"]
        assert not hass.config_entries.flow.async_progress()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_real_reload_preserves_entity_and_device_registries(hass, entry, portal, recorder_mock):
    """Reálné platformy a reload zachovají registry včetně uživatelského ID.

    Mock dat nahrazuje měření portálu; historický import je vypnutý, protože
    tento test ověřuje životní cyklus entit a nesmí zapisovat statistiky.
    Setup/unload/reload, tvorbu entit i oba registry zajišťuje skutečný HA.
    """
    data = WaterMeterData(
        PLACE,
        (MeterReading(datetime(2026, 10, 19, 10), 100.0),),
        0.0,
    )
    with (
        patch(f"{COMPONENT}.coordinator.VsChrudimCoordinator._async_update_data", return_value=data),
        patch(f"{COMPONENT}.coordinator.VsChrudimCoordinator.async_start_history_backfill", return_value=False),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        registry = er.async_get(hass)
        meter_id = registry.async_get_entity_id("sensor", DOMAIN, "place-a_meter_state")
        assert meter_id is not None
        registry.async_update_entity(meter_id, new_entity_id="sensor.custom_watermeter")

        def snapshot():
            return {
                item.entity_id: (item.id, item.unique_id, item.device_id, item.config_entry_id)
                for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            }

        before = snapshot()
        devices_before = {
            item.id: (item.identifiers, item.config_entry_id)
            for item in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        }
        # Původních 20 entit a nové tlačítko pro odkaz na generátor.
        assert len(before) == 21
        assert any(data[1] == "place-a_generate_dashboard" for data in before.values())
        result = await select_place(hass, await authenticate(hass, await start_reauth(hass, entry)))
        assert result["reason"] == "reauth_successful"
        assert entry.state is ConfigEntryState.LOADED
        assert snapshot() == before
        assert {
            item.id: (item.identifiers, item.config_entry_id)
            for item in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        } == devices_before
        assert hass.states.get("sensor.custom_watermeter").state == "100.0"
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
