"""Tests for the config and options flows."""

from __future__ import annotations

from functools import partial
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rinnai_touch_climate.const import (
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
)
from custom_components.rinnai_touch_climate.protocol import discover

from .fake_module import FakeModule

CONFIG_FLOW = "custom_components.rinnai_touch_climate.config_flow"


def _discover_on(udp_port: int):
    return patch(f"{CONFIG_FLOW}.discover", partial(discover, port=udp_port))


async def test_discovered_host_is_prefilled(
    hass: HomeAssistant, module: FakeModule, udp_port: int
) -> None:
    """A broadcasting module's address is offered as the default."""
    with _discover_on(udp_port), patch(f"{CONFIG_FLOW}.DISCOVERY_TIMEOUT", 0.3):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"found": "127.0.0.1"}
    assert result["data_schema"]({})[CONF_HOST] == "127.0.0.1"

    with patch(
        "custom_components.rinnai_touch_climate.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Rinnai Touch (127.0.0.1)"
    assert result["data"] == {CONF_HOST: "127.0.0.1"}


async def test_manual_host_when_nothing_broadcasts(
    hass: HomeAssistant, udp_port: int
) -> None:
    """With the module busy (silent), the host can be typed in."""
    with _discover_on(udp_port), patch(f"{CONFIG_FLOW}.DISCOVERY_TIMEOUT", 0.2):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["description_placeholders"] == {"found": "-"}
    assert result["data_schema"]({CONF_HOST: "x"})[CONF_HOST] == "x"

    with patch(
        "custom_components.rinnai_touch_climate.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: " 192.168.1.50 "}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "192.168.1.50"}


async def test_already_configured(hass: HomeAssistant, udp_port: int) -> None:
    """The same host cannot be added twice."""
    MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.168.1.50"}).add_to_hass(hass)
    with _discover_on(udp_port), patch(f"{CONFIG_FLOW}.DISCOVERY_TIMEOUT", 0.1):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.50"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant) -> None:
    """The temperature sensor can be set and cleared."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.168.1.50"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TEMPERATURE_SENSOR: "sensor.hall"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_TEMPERATURE_SENSOR: "sensor.hall"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert entry.options == {}
