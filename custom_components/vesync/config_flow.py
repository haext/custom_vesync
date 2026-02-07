"""Config flow utilities."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.service_info import dhcp
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from pyvesync.vesync import VeSync

from .const import DOMAIN, POLLING_INTERVAL

_LOGGER = logging.getLogger(__name__)


DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(POLLING_INTERVAL, default=60): int,
    }
)


def reauth_schema(
    def_username: str | vol.UNDEFINED = vol.UNDEFINED,
    def_password: str | vol.UNDEFINED = vol.UNDEFINED,
    def_poll: int | vol.UNDEFINED = 60,
) -> dict[vol.Marker, Any]:
    """Return schema for reauth flow with optional default value."""

    return {
        vol.Required(CONF_USERNAME, default=def_username): cv.string,
        vol.Required(CONF_PASSWORD, default=def_password): cv.string,
        vol.Required(POLLING_INTERVAL, default=def_poll): int,
    }


class VeSyncFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 2

    entry: config_entries.ConfigEntry | None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> VeSyncOptionsFlowHandler:
        """Get the options flow for this handler."""

        return VeSyncOptionsFlowHandler()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Handle re-authentication with VeSync."""

        self.entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm re-authentication with VeSync."""

        errors: dict[str, str] = {}
        if user_input:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            polling_interval = user_input[POLLING_INTERVAL]
            manager = VeSync(username, password)
            login = await self.hass.async_add_executor_job(manager.login)
            if not login:
                errors["base"] = "invalid_auth"
            else:
                assert self.entry is not None

                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={
                        **self.entry.data,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                    options={
                        POLLING_INTERVAL: polling_interval,
                    },
                )

                await self.hass.config_entries.async_reload(self.entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                reauth_schema(
                    self.entry.data[CONF_USERNAME],
                    self.entry.data[CONF_PASSWORD],
                    self.entry.options[POLLING_INTERVAL],
                )
            ),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow start."""

        errors: dict[str, str] = {}

        if user_input:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            polling_interval = user_input[POLLING_INTERVAL]
            manager = VeSync(username, password)
            login = await self.hass.async_add_executor_job(manager.login)
            if not login:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(f"{username}-{manager.account_id}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: password},
                    options={
                        POLLING_INTERVAL: polling_interval,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_dhcp(self, discovery_info: dhcp.DhcpServiceInfo) -> FlowResult:
        """Handle DHCP discovery."""
        hostname = discovery_info.hostname

        _LOGGER.debug("DHCP discovery detected device %s", hostname)
        self.context["title_placeholders"] = {"gateway_id": hostname}
        return await self.async_step_user()


class VeSyncOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle VeSync integration options."""

    async def async_step_init(self, user_input=None):
        """Manage options."""

        return await self.async_step_vesync_options()

    async def async_step_vesync_options(self, user_input=None):
        """Manage the VeSync options."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = {
            vol.Required(
                POLLING_INTERVAL,
                default=self.config_entry.options.get(POLLING_INTERVAL, 60),
            ): int,
        }

        return self.async_show_form(
            step_id="vesync_options", data_schema=vol.Schema(options)
        )
