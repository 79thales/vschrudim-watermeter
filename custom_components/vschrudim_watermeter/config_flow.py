"""Config and reauthentication flow."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    VsChrudimAuthError,
    VsChrudimClient,
    VsChrudimConnectionError,
    VsChrudimError,
)
from .const import (
    CONF_FAILURE_THRESHOLD,
    CONF_MISSING_RETRY_ATTEMPTS,
    CONF_NOTIFY_MISSING,
    CONF_NOTIFY_UNAVAILABLE,
    CONF_PLACE,
    CONF_PRICE_PER_M3,
    CONF_RETRY_DELAY,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE_DELAY_WARNING_HOURS,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_MISSING_RETRY_ATTEMPTS,
    DEFAULT_NOTIFY_MISSING,
    DEFAULT_NOTIFY_UNAVAILABLE,
    DEFAULT_PRICE_PER_M3,
    DEFAULT_RETRY_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOURCE_DELAY_WARNING_HOURS,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Samostatný odkaz nenahrazuje stávající reconfigure ani reauth."""
        return DashboardOptionsFlow(config_entry)
    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._places = []

    async def _validate(self, user_input: Mapping[str, str]) -> None:
        session = async_create_clientsession(
            self.hass,
            auto_cleanup=False,
            cookie_jar=aiohttp.CookieJar(),
        )
        try:
            client = VsChrudimClient(
                session,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            self._places = await client.async_get_places()
            self._credentials = dict(user_input)
        finally:
            session.detach()

    async def async_step_user(self, user_input: Mapping[str, str] | None = None):
        """Ověřit přihlášení; chyby ponechají formulář otevřený bez zápisu dat."""
        errors = {}
        if user_input:
            try:
                await self._validate(user_input)
                if not self._places:
                    errors["base"] = "no_places"
                elif self.source == config_entries.SOURCE_REAUTH and not any(
                    place.identifier
                    == (
                        self._get_reauth_entry().data[CONF_PLACE]["evidence_number"]
                        or self._get_reauth_entry().data[CONF_PLACE]["technical_number"]
                    )
                    for place in self._places
                ):
                    # Chybějící původní místo není oprávnění přepnout konfiguraci
                    # na jiné. Uživatel může zkusit správné přihlášení znovu.
                    errors["base"] = "reauth_place_not_found"
                else:
                    return await self.async_step_place()
            except VsChrudimAuthError:
                errors["base"] = "invalid_auth"
            except VsChrudimConnectionError:
                errors["base"] = "cannot_connect"
            except VsChrudimError:
                errors["base"] = "unknown"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}), errors=errors)

    async def async_step_place(self, user_input: Mapping[str, str] | None = None):
        """Vytvořit nové místo, nebo obnovit přihlášení ke stejné identitě.

        Identita zůstává tvořena normalizovaným uživatelem a ID místa.
        Reauth aktualizuje pouze záznam z kontextu toku; kontrola duplicity
        patří až do větve nové instalace. Helper naplánuje reload a ukončí
        tok, zatímco options, název a identifikátory ponechá beze změny.
        """
        choices = {place.identifier: place.address or place.technical_number for place in self._places}
        if user_input:
            selected = next(place for place in self._places if place.identifier == user_input[CONF_PLACE])
            unique_id = f"{self._credentials[CONF_USERNAME].casefold()}_{selected.identifier}"
            await self.async_set_unique_id(unique_id)
            data = {**self._credentials, CONF_PLACE: asdict(selected)}
            if self.source == config_entries.SOURCE_REAUTH:
                self._abort_if_unique_id_mismatch()
                # Parametr data nahrazuje celý slovník. Zachováme proto i
                # nesouvisející klíče starších či budoucích konfigurací.
                data = {**self._get_reauth_entry().data, **data}
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data=data
                )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=choices[selected.identifier], data=data, options={CONF_PRICE_PER_M3: DEFAULT_PRICE_PER_M3})
        return self.async_show_form(step_id="place", data_schema=vol.Schema({vol.Required(CONF_PLACE): vol.In(choices)}))

    async def async_step_reauth(self, entry_data: Mapping[str, str]):
        """Zahájit nový tok nad uloženým záznamem, také po restartu HA.

        Cílový záznam určuje entry_id v kontextu HA, nikoli shoda účtu ani
        odkaz uchovaný ve staré instanci toku. entry_data obsahuje původní
        údaje; nové heslo musí uživatel znovu zadat a nechat ověřit.
        """
        self._get_reauth_entry()
        return await self.async_step_user()

    async def async_step_reconfigure(self, user_input: Mapping[str, str] | None = None):
        entry = self._get_reconfigure_entry()
        if user_input:
            self.hass.config_entries.async_update_entry(
                entry,
                options={**entry.options, **user_input},
            )
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reconfigure_successful")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=entry.options.get(CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60))): vol.All(vol.Coerce(int), vol.Range(min=int(MIN_SCAN_INTERVAL.total_seconds() / 60), max=1440)),
                    vol.Required(CONF_PRICE_PER_M3, default=entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3)): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
                    vol.Required(CONF_NOTIFY_UNAVAILABLE, default=entry.options.get(CONF_NOTIFY_UNAVAILABLE, DEFAULT_NOTIFY_UNAVAILABLE)): bool,
                    vol.Required(CONF_FAILURE_THRESHOLD, default=entry.options.get(CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD)): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
                    vol.Required(CONF_NOTIFY_MISSING, default=entry.options.get(CONF_NOTIFY_MISSING, DEFAULT_NOTIFY_MISSING)): bool,
                    vol.Required(CONF_MISSING_RETRY_ATTEMPTS, default=entry.options.get(CONF_MISSING_RETRY_ATTEMPTS, DEFAULT_MISSING_RETRY_ATTEMPTS)): vol.All(vol.Coerce(int), vol.Range(min=0, max=5)),
                    vol.Required(CONF_RETRY_DELAY, default=entry.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)): vol.All(vol.Coerce(int), vol.Range(min=5, max=900)),
                    vol.Required(CONF_SOURCE_DELAY_WARNING_HOURS, default=entry.options.get(CONF_SOURCE_DELAY_WARNING_HOURS, DEFAULT_SOURCE_DELAY_WARNING_HOURS)): vol.All(vol.Coerce(int), vol.Range(min=0, max=8760)),
                }
            ),
        )


class DashboardOptionsFlow(config_entries.OptionsFlow):
    """Otevřít pomocný panel bez změny uložených nastavení integrace."""

    def __init__(self, entry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input=None):
        """Zobrazit bezpečný odkaz, po zavření toku nic neukládat."""
        if user_input is not None:
            return self.async_abort(reason="dashboard_closed")
        from .dashboard import PANEL_PATH

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
            description_placeholders={"url": f"/{PANEL_PATH}?entry_id={self._entry.entry_id}"},
        )
