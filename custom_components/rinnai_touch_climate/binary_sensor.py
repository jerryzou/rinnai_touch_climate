"""Fault binary sensor for the Rinnai Touch controller."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RinnaiTouchConfigEntry
from .entity import RinnaiTouchEntity
from .protocol import Fault


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RinnaiTouchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the fault sensor."""
    async_add_entities([RinnaiFaultBinarySensor(entry)])


class RinnaiFaultBinarySensor(RinnaiTouchEntity, BinarySensorEntity):
    """On while the controller reports a fault (SYST.FLT)."""

    _attr_translation_key = "fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: RinnaiTouchConfigEntry) -> None:
        """Set up the sensor."""
        super().__init__(entry, "fault")

    @property
    def _fault(self) -> Fault | None:
        return Fault.from_status(self.client.status)

    @property
    def is_on(self) -> bool | None:
        """Return True while a fault is reported."""
        fault = self._fault
        return None if fault is None else fault.detected

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the fault details the controller gives."""
        fault = self._fault
        if fault is None or not fault.detected:
            return None
        return {
            "appliance": fault.appliance,
            "unit": fault.unit,
            "severity": fault.severity,
            "code": fault.code,
        }
