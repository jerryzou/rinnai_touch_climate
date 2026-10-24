"""Diagnostics for the Rinnai Touch Climate integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import RinnaiTouchConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RinnaiTouchConfigEntry
) -> dict[str, Any]:
    """Return the connection state and the module's raw status."""
    client = entry.runtime_data
    address = client.address
    return {
        "host": client.host,
        "tcp_port": address.port if address else None,
        "connection_state": client.state.value,
        "connection_count": client.connection_count,
        "last_error": client.last_error,
        "options": dict(entry.options),
        "status": client.status.as_dict(),
    }
