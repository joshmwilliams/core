"""Config flow to configure webostv component."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from aiowebostv import WebOsTvPairError
import voluptuous as vol

from homeassistant import config_entries, data_entry_flow
from homeassistant.components import ssdp
from homeassistant.const import (
    CONF_CLIENT_SECRET,
    CONF_CUSTOMIZE,
    CONF_HOST,
    CONF_NAME,
    CONF_UNIQUE_ID,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import async_control_connect
from .const import CONF_SOURCES, DEFAULT_NAME, DOMAIN, WEBOSTV_EXCEPTIONS

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)


class FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """WebosTV configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize workflow."""
        self._host: str = ""
        self._name: str = ""
        self._uuid: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_import(self, import_info: dict[str, Any]) -> FlowResult:
        """Set the config entry up from yaml."""
        self._host = import_info[CONF_HOST]
        self._name = import_info.get(CONF_NAME) or import_info[CONF_HOST]
        await self.async_set_unique_id(
            import_info[CONF_UNIQUE_ID], raise_on_progress=False
        )
        data = {
            CONF_HOST: self._host,
            CONF_CLIENT_SECRET: import_info[CONF_CLIENT_SECRET],
        }
        self._abort_if_unique_id_configured()

        options: dict[str, list[str]] | None = None
        if sources := import_info.get(CONF_CUSTOMIZE, {}).get(CONF_SOURCES):
            if not isinstance(sources, list):
                sources = [s.strip() for s in sources.split(",")]
            options = {CONF_SOURCES: sources}

        _LOGGER.debug("WebOS Smart TV host %s imported from YAML config", self._host)
        return self.async_create_entry(title=self._name, data=data, options=options)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._name = user_input[CONF_NAME]
            return await self.async_step_pairing()

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    @callback
    def _async_check_configured_entry(self) -> None:
        """Check if entry is configured, update unique_id if needed."""
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data[CONF_HOST] != self._host:
                continue

            if self._uuid and not entry.unique_id:
                _LOGGER.debug(
                    "Updating unique_id for host %s, unique_id: %s",
                    self._host,
                    self._uuid,
                )
                self.hass.config_entries.async_update_entry(entry, unique_id=self._uuid)

            raise data_entry_flow.AbortFlow("already_configured")

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Display pairing form."""
        self._async_check_configured_entry()

        self.context[CONF_HOST] = self._host
        self.context["title_placeholders"] = {"name": self._name}
        errors = {}

        if (
            self.context["source"] == config_entries.SOURCE_IMPORT
            or user_input is not None
        ):
            try:
                client = await async_control_connect(self._host, None)
            except WebOsTvPairError:
                return self.async_abort(reason="error_pairing")
            except WEBOSTV_EXCEPTIONS:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    client.hello_info["deviceUUID"], raise_on_progress=False
                )
                self._abort_if_unique_id_configured({CONF_HOST: self._host})
                data = {CONF_HOST: self._host, CONF_CLIENT_SECRET: client.client_key}
                return self.async_create_entry(title=self._name, data=data)

        return self.async_show_form(step_id="pairing", errors=errors)

    async def async_step_ssdp(self, discovery_info: ssdp.SsdpServiceInfo) -> FlowResult:
        """Handle a flow initialized by discovery."""
        assert discovery_info.ssdp_location
        host = urlparse(discovery_info.ssdp_location).hostname
        assert host
        self._host = host
        self._name = discovery_info.upnp[ssdp.ATTR_UPNP_FRIENDLY_NAME]

        uuid = discovery_info.upnp[ssdp.ATTR_UPNP_UDN]
        assert uuid
        if uuid.startswith("uuid:"):
            uuid = uuid[5:]
        await self.async_set_unique_id(uuid)
        self._abort_if_unique_id_configured({CONF_HOST: self._host})

        for progress in self._async_in_progress():
            if progress.get("context", {}).get(CONF_HOST) == self._host:
                return self.async_abort(reason="already_in_progress")

        self._uuid = uuid
        return await self.async_step_pairing()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.options = config_entry.options
        self.host = config_entry.data[CONF_HOST]
        self.key = config_entry.data[CONF_CLIENT_SECRET]

    async def async_step_init(self, user_input: ConfigType | None = None) -> FlowResult:
        """Manage the options."""
        errors = {}
        if user_input is not None:
            options_input = {CONF_SOURCES: user_input[CONF_SOURCES]}
            return self.async_create_entry(title="", data=options_input)
        # Get sources
        sources_list = await async_get_sources(self.host, self.key)
        if not sources_list:
            errors["base"] = "cannot_retrieve"

        sources = [s for s in self.options.get(CONF_SOURCES, []) if s in sources_list]
        if not sources:
            sources = sources_list

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SOURCES,
                    description={"suggested_value": sources},
                ): cv.multi_select({source: source for source in sources_list}),
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=options_schema, errors=errors
        )


async def async_get_sources(host: str, key: str) -> list[str]:
    """Construct sources list."""
    try:
        client = await async_control_connect(host, key)
    except WEBOSTV_EXCEPTIONS:
        return []

    return list(
        dict.fromkeys(  # Preserve order when filtering duplicates
            [
                *(app["title"] for app in client.apps.values()),
                *(app["label"] for app in client.inputs.values()),
            ]
        )
    )
