"""Climate entity for the Rinnai Touch evaporative cooler.

``cool`` is the cooler's Auto mode: the target temperature is its comfort
level (19-34, sent as is). ``fan_only`` is Manual mode, with the fan speed
(1-16) as the fan mode. A comfort level set outside Auto, or a fan speed
set outside Manual, is kept and applied when that mode is next selected.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import RinnaiTouchConfigEntry
from .const import CONF_TEMPERATURE_SENSOR
from .entity import RinnaiTouchEntity, command
from .protocol import evap
from .protocol.evap import COMFORT_MAX, COMFORT_MIN, FAN_SPEED_MAX, FAN_SPEED_MIN

FAN_MODES = [str(speed) for speed in range(FAN_SPEED_MIN, FAN_SPEED_MAX + 1)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RinnaiTouchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the climate entity."""
    # Only evaporative cooling is implemented; heating and add-on cooling
    # (HGOM/CGOM) would get their own entity classes here.
    async_add_entities([RinnaiEvapClimate(entry)])


class RinnaiEvapClimate(RinnaiTouchEntity, ClimateEntity):
    """The evaporative cooler."""

    _attr_name = None
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY]
    _attr_fan_modes = FAN_MODES
    _attr_min_temp = COMFORT_MIN
    _attr_max_temp = COMFORT_MAX
    _attr_target_temperature_step = 1
    _attr_precision = PRECISION_TENTHS
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, entry: RinnaiTouchConfigEntry) -> None:
        """Set up the climate entity."""
        super().__init__(entry, "climate")
        self._entry = entry
        self._sensor_temperature: float | None = None
        self._unsub_sensor: Any = None
        self._comfort: int | None = None
        self._fan_speed: int | None = None
        self._pending_comfort: int | None = None
        self._pending_fan_speed: int | None = None
        self._last_on_mode = HVACMode.COOL

    async def async_added_to_hass(self) -> None:
        """Follow the status stream and the optional temperature sensor."""
        await super().async_added_to_hass()
        self.async_on_remove(self._entry.add_update_listener(self._options_updated))
        self.async_on_remove(self._stop_sensor)
        self._track_sensor()
        self._read_status()

    @property
    def _state(self) -> evap.EvapState:
        return evap.EvapState.from_status(self.client.status)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return off, cool (Auto) or fan_only (Manual)."""
        state = self._state
        if state.power is False:
            return HVACMode.OFF
        if state.power is None or state.auto is None:
            return None
        return HVACMode.COOL if state.auto else HVACMode.FAN_ONLY

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return what the cooler is doing, as the Homebridge plugin reports it.

        Auto counts as cooling; in Manual the water pump means cooling and
        the fan alone means fan.
        """
        state = self._state
        if state.power is False:
            return HVACAction.OFF
        if state.power is None or state.auto is None:
            return None
        if state.auto or state.pump:
            return HVACAction.COOLING
        return HVACAction.FAN if state.fan else HVACAction.IDLE

    @property
    def target_temperature(self) -> float | None:
        """Return the comfort level."""
        level = self._pending_comfort or self._comfort
        return None if level is None else float(level)

    @property
    def fan_mode(self) -> str | None:
        """Return the manual fan speed."""
        speed = self._pending_fan_speed or self._fan_speed
        return None if speed is None else str(speed)

    @property
    def current_temperature(self) -> float | None:
        """Return the chosen sensor's temperature, else the controller's."""
        if self._entry.options.get(CONF_TEMPERATURE_SENSOR):
            return self._sensor_temperature
        return self._state.temperature

    def _handle_update(self) -> None:
        self._read_status()
        super()._handle_update()

    def _read_status(self) -> None:
        # The module may omit or zero a value while it is not in use, so
        # remember the last valid one.
        state = self._state
        if state.comfort is not None and COMFORT_MIN <= state.comfort <= COMFORT_MAX:
            self._comfort = state.comfort
        if (
            state.fan_speed is not None
            and FAN_SPEED_MIN <= state.fan_speed <= FAN_SPEED_MAX
        ):
            self._fan_speed = state.fan_speed
        mode = self.hvac_mode
        if mode in (HVACMode.COOL, HVACMode.FAN_ONLY):
            self._last_on_mode = mode
        # Once in that mode (set from here or on the wall controller), the
        # module's own value is the truth.
        if mode == HVACMode.COOL:
            self._pending_comfort = None
        elif mode == HVACMode.FAN_ONLY:
            self._pending_fan_speed = None

    @command
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch off, to Auto (cool) or to Manual (fan_only)."""
        if hvac_mode == HVACMode.OFF:
            await self.client.async_send(evap.power(False))
        elif hvac_mode == HVACMode.COOL:
            await self.client.async_send(*evap.turn_on_auto(self._pending_comfort))
            self._pending_comfort = None
        else:
            await self.client.async_send(*evap.turn_on_manual(self._pending_fan_speed))
            self._pending_fan_speed = None

    @command
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the comfort level, now if in Auto, otherwise for later."""
        level = round(kwargs[ATTR_TEMPERATURE])
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None and mode != HVACMode.COOL:
            self._pending_comfort = level
            await self.async_set_hvac_mode(mode)
            return
        if mode == HVACMode.COOL or self.hvac_mode == HVACMode.COOL:
            self._pending_comfort = None
            await self.client.async_send(*evap.turn_on_auto(level))
        else:
            self._pending_comfort = level
            self.async_write_ha_state()

    @command
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed, now if in Manual, otherwise for later."""
        speed = int(fan_mode)
        if self.hvac_mode == HVACMode.FAN_ONLY:
            self._pending_fan_speed = None
            await self.client.async_send(evap.fan_speed(speed))
        else:
            self._pending_fan_speed = speed
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Switch on in the mode it last ran in."""
        await self.async_set_hvac_mode(self._last_on_mode)

    async def async_turn_off(self) -> None:
        """Switch off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    # Optional external temperature sensor

    async def _options_updated(
        self, hass: HomeAssistant, entry: RinnaiTouchConfigEntry
    ) -> None:
        self._stop_sensor()
        self._track_sensor()
        self.async_write_ha_state()

    def _track_sensor(self) -> None:
        sensor = self._entry.options.get(CONF_TEMPERATURE_SENSOR)
        self._sensor_temperature = None
        if not sensor:
            return
        self._unsub_sensor = async_track_state_change_event(
            self.hass, sensor, self._sensor_changed
        )
        if (state := self.hass.states.get(sensor)) is not None:
            self._sensor_temperature = _as_float(state.state)

    @callback
    def _stop_sensor(self) -> None:
        if self._unsub_sensor is not None:
            self._unsub_sensor()
            self._unsub_sensor = None

    @callback
    def _sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        self._sensor_temperature = (
            None if new_state is None else _as_float(new_state.state)
        )
        self.async_write_ha_state()


def _as_float(value: str) -> float | None:
    if value in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        return float(value)
    except ValueError:
        return None
