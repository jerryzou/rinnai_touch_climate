"""Tests for setting up, reloading and unloading the integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rinnai_touch_climate.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import wait_until
from .fake_module import FakeModule

CLIMATE = "climate.rinnai_touch"
CONNECTION = "sensor.rinnai_touch_connection"


async def test_setup_without_module(
    hass: HomeAssistant, config_entry: MockConfigEntry, fast_client: None
) -> None:
    """Setup succeeds with the module silent; entities wait, unavailable."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    await wait_until(lambda: hass.states.get(CONNECTION).state == "waiting_for_module")
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(config_entry.entry_id)


async def test_reload_waits_for_the_slot(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """Reloading closes the connection and reconnects through the UDP gate.

    Reconnecting at once is what gets the real module to refuse
    (pyrinnaitouch #102); here the client waits for the broadcast.
    """
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    await wait_until(lambda: setup_integration.runtime_data.connected)
    assert module.connections == 2
    assert module.disconnects == 1
    assert module.rejected == 0


async def test_unload_closes_connection(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """Unloading closes the socket so the module frees its slot."""
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    assert setup_integration.state is ConfigEntryState.NOT_LOADED
    await wait_until(lambda: not module.connected)


async def test_stop_event_closes_connection(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """Home Assistant shutting down closes the connection."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    await wait_until(lambda: not module.connected)


async def test_entities_unavailable_while_reconnecting(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """A silent module makes entities unavailable until reconnected."""
    module.go_silent()
    await wait_until(lambda: hass.states.get(CLIMATE).state == STATE_UNAVAILABLE)
    assert hass.states.get(CONNECTION).state != "connected"
    await wait_until(lambda: hass.states.get(CLIMATE).state == "fan_only")
    assert hass.states.get(CONNECTION).state == "connected"
    assert module.rejected == 0


async def test_diagnostics(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """Diagnostics carry the connection and the raw status."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)
    assert diagnostics["connection_state"] == "connected"
    assert diagnostics["tcp_port"] == module.port
    assert diagnostics["status"]["ECOM"]["GSO"]["FL"] == "02"
