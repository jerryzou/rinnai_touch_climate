"""Config flow for the Rinnai Touch Climate integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig
import voluptuous as vol

from .const import CONF_TEMPERATURE_SENSOR, DISCOVERY_TIMEOUT, DOMAIN
from .protocol import discover


class RinnaiTouchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Rinnai Touch module."""

    VERSION = 1

    def __init__(self) -> None:
        """Start without having listened for modules."""
        self._discovered: list[str] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen for module broadcasts and offer the host found."""
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            self._async_abort_entries_match({CONF_HOST: host})
            return self.async_create_entry(
                title=f"Rinnai Touch ({host})", data={CONF_HOST: host}
            )

        if self._discovered is None:
            try:
                modules = await discover(DISCOVERY_TIMEOUT)
            except OSError:
                modules = []
            configured = {
                entry.data[CONF_HOST] for entry in self._async_current_entries()
            }
            self._discovered = [m.host for m in modules if m.host not in configured]

        found = self._discovered[0] if self._discovered else ""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=found): str}),
            description_placeholders={
                "found": ", ".join(self._discovered) if self._discovered else "-"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return RinnaiTouchOptionsFlow()


class RinnaiTouchOptionsFlow(OptionsFlow):
    """Pick the sensor that provides the current temperature."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(CONF_TEMPERATURE_SENSOR): EntitySelector(
                    EntitySelectorConfig(
                        domain="sensor", device_class=SensorDeviceClass.TEMPERATURE
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
