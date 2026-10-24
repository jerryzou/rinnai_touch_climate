"""Water pump switch for the Rinnai Touch evaporative cooler."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RinnaiTouchConfigEntry
from .entity import RinnaiTouchEntity, command
from .protocol import evap


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RinnaiTouchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the pump switch."""
    async_add_entities([RinnaiEvapPumpSwitch(entry)])


class RinnaiEvapPumpSwitch(RinnaiTouchEntity, SwitchEntity):
    """The water pump; turning it on switches the cooler to Manual."""

    _attr_translation_key = "pump"

    def __init__(self, entry: RinnaiTouchConfigEntry) -> None:
        """Set up the switch."""
        super().__init__(entry, "pump")

    @property
    def is_on(self) -> bool | None:
        """Return True while the pump is set to run."""
        state = evap.EvapState.from_status(self.client.status)
        if state.power is False:
            return False
        return state.pump

    @command
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Run the pump (in Manual mode)."""
        await self.client.async_send(*evap.set_pump(True))

    @command
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the pump."""
        await self.client.async_send(*evap.set_pump(False))
