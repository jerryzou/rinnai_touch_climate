"""Constants for the Rinnai Touch Climate integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "rinnai_touch_climate"

CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"

# How long the config flow listens for module broadcasts (about 1 Hz).
DISCOVERY_TIMEOUT: Final = 3.0
