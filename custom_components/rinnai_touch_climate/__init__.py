"""The Rinnai Touch Climate integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant

from .protocol import RinnaiTouchClient

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]

type RinnaiTouchConfigEntry = ConfigEntry[RinnaiTouchClient]


async def async_setup_entry(hass: HomeAssistant, entry: RinnaiTouchConfigEntry) -> bool:
    """Start the connection; entities stay unavailable until it is up.

    Setup does not wait for the module: when another client (such as
    Homebridge) holds its single connection, the client keeps waiting for
    the module's broadcast in the background.
    """
    client = RinnaiTouchClient(entry.data[CONF_HOST])
    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    client.start()

    async def _stop(_: Event) -> None:
        # Close cleanly at shutdown so the module frees its slot.
        await client.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RinnaiTouchConfigEntry
) -> bool:
    """Close the connection and unload the platforms."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.stop()
    return unloaded
