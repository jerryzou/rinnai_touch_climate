"""Connection state sensor for the Rinnai Touch module."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RinnaiTouchConfigEntry
from .entity import RinnaiTouchEntity
from .protocol import ConnectionState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RinnaiTouchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the connection sensor."""
    async_add_entities([RinnaiConnectionSensor(entry)])


class RinnaiConnectionSensor(RinnaiTouchEntity, SensorEntity):
    """Where the client is in its connection cycle.

    ``waiting_for_module`` for a long time means the module is not
    broadcasting: another client (such as Homebridge) is connected, or the
    module is offline.
    """

    _attr_translation_key = "connection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [state.value for state in ConnectionState]

    def __init__(self, entry: RinnaiTouchConfigEntry) -> None:
        """Set up the sensor."""
        super().__init__(entry, "connection")

    @property
    def available(self) -> bool:
        """Always available: it reports the connection itself."""
        return True

    @property
    def native_value(self) -> str:
        """Return the connection state."""
        return self.client.state.value
