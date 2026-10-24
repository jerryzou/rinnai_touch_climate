"""Base entity for the Rinnai Touch Climate integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Concatenate

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import RinnaiTouchConfigEntry
from .const import DOMAIN
from .protocol import CommandFailedError, NotConnectedError, RinnaiTouchClient


class RinnaiTouchEntity(Entity):
    """An entity fed by the module's status stream."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: RinnaiTouchConfigEntry, key: str) -> None:
        """Set up the entity for a config entry."""
        self.client: RinnaiTouchClient = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Rinnai",
            model="Touch WiFi module (N-BW2)",
            name="Rinnai Touch",
        )

    @property
    def available(self) -> bool:
        """Return True while the module is connected."""
        return self.client.connected

    async def async_added_to_hass(self) -> None:
        """Follow the status stream."""
        await super().async_added_to_hass()
        self.async_on_remove(self.client.add_listener(self._handle_update))

    def _handle_update(self) -> None:
        self.async_write_ha_state()


def command[E: RinnaiTouchEntity, **P](
    func: Callable[Concatenate[E, P], Awaitable[None]],
) -> Callable[Concatenate[E, P], Awaitable[None]]:
    """Turn client errors in a service call into HomeAssistantError."""

    @wraps(func)
    async def wrapper(self: E, *args: P.args, **kwargs: P.kwargs) -> None:
        try:
            await func(self, *args, **kwargs)
        except NotConnectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_connected"
            ) from err
        except CommandFailedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    return wrapper
