"""Config and reauthentication flow."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import asdict
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from .api import VsChrudimAuthError, VsChrudimClient, VsChrudimConnectionError, VsChrudimError
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
    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._places = []
        self._reauth_entry = None

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
        errors = {}
        if user_input:
            try:
                await self._validate(user_input)
                if not self._places:
                    errors["base"] = "no_places"
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
        choices = {place.identifier: place.address or place.technical_number for place in self._places}
        if user_input:
            selected = next(place for place in self._places if place.identifier == user_input[CONF_PLACE])
            unique_id = f"{self._credentials[CONF_USERNAME].casefold()}_{selected.identifier}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            data = {**self._credentials, CONF_PLACE: asdict(selected)}
            if self._reauth_entry:
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            return self.async_create_entry(title=choices[selected.identifier], data=data, options={CONF_PRICE_PER_M3: DEFAULT_PRICE_PER_M3})
        return self.async_show_form(step_id="place", data_schema=vol.Schema({vol.Required(CONF_PLACE): vol.In(choices)}))

    async def async_step_reauth(self, entry_data: Mapping[str, str]):
        self._reauth_entry = self._get_reauth_entry()
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
