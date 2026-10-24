"""Tests for the climate, switch and sensor entities."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rinnai_touch_climate.const import CONF_TEMPERATURE_SENSOR

from .conftest import wait_until
from .fake_module import FakeModule

CLIMATE = "climate.rinnai_touch"
PUMP = "switch.rinnai_touch_pump"
FAULT = "binary_sensor.rinnai_touch_fault"
CONNECTION = "sensor.rinnai_touch_connection"


async def _climate(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE, **data}, blocking=True
    )


def _gso(module: FakeModule) -> dict[str, str]:
    return module.status["ECOM"]["GSO"]


async def test_initial_state(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The real Manual-mode status maps onto the entities."""
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.FAN_ONLY
    assert state.attributes[ATTR_FAN_MODE] == "2"
    assert state.attributes["hvac_modes"] == ["off", "cool", "fan_only"]
    assert state.attributes["min_temp"] == 19
    assert state.attributes["max_temp"] == 34
    # MT is 999: the controller has no temperature reading.
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] is None
    assert hass.states.get(PUMP).state == STATE_ON
    assert hass.states.get(FAULT).state == STATE_OFF
    assert hass.states.get(CONNECTION).state == "connected"


async def test_cool_sets_auto_and_comfort(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """cool = Auto; the target temperature is the comfort level, 1:1."""
    await _climate(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
    assert _gso(module)["OP"] == "A"
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == HVACMode.COOL

    await _climate(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 25})
    assert _gso(module)["SP"] == "25"
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 25


async def test_set_temperature_with_mode(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """set_temperature with hvac_mode cool switches to Auto at that level."""
    await _climate(
        hass,
        SERVICE_SET_TEMPERATURE,
        **{ATTR_TEMPERATURE: 22, ATTR_HVAC_MODE: HVACMode.COOL},
    )
    assert (_gso(module)["OP"], _gso(module)["SP"]) == ("A", "22")


async def test_comfort_set_outside_auto_is_kept(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """In Manual, a new comfort level waits until cool is selected."""
    await _climate(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 30})
    assert module.commands == []
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 30

    await _climate(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
    assert (_gso(module)["OP"], _gso(module)["SP"]) == ("A", "30")


async def test_off_and_fan_only_with_pending_speed(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """off switches off; a fan speed set while off applies on fan_only."""
    await _climate(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})
    assert _gso(module)["SW"] == "F"
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == HVACMode.OFF
    assert hass.states.get(PUMP).state == STATE_OFF

    await _climate(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "12"})
    assert _gso(module)["FL"] == "02"
    assert hass.states.get(CLIMATE).attributes[ATTR_FAN_MODE] == "12"

    await _climate(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.FAN_ONLY})
    gso = _gso(module)
    assert (gso["SW"], gso["OP"], gso["FS"], gso["FL"]) == ("N", "M", "N", "12")


async def test_fan_speed_in_manual(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """In Manual the fan speed is sent straight away."""
    await _climate(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "16"})
    assert _gso(module)["FL"] == "16"


async def test_turn_off_and_on_restores_mode(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """turn_on goes back to the mode the cooler last ran in."""
    await _climate(hass, SERVICE_TURN_OFF)
    assert _gso(module)["SW"] == "F"
    await _climate(hass, SERVICE_TURN_ON)
    assert (_gso(module)["SW"], _gso(module)["OP"]) == ("N", "M")


async def test_pump_switch(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """The pump switch sets PS; turning it on needs Manual."""
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: PUMP}, blocking=True
    )
    assert _gso(module)["PS"] == "F"
    await hass.async_block_till_done()
    assert hass.states.get(PUMP).state == STATE_OFF

    await _climate(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: PUMP}, blocking=True
    )
    assert (_gso(module)["OP"], _gso(module)["PS"]) == ("M", "N")


async def test_unconfirmed_command_raises(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """A command the module never confirms surfaces as an error."""
    module.ignore_commands = 3
    with pytest.raises(HomeAssistantError, match="did not carry out"):
        await _climate(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})


async def test_fault(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """A reported fault turns the fault sensor on with its details."""
    module.status["SYST"]["FLT"] = {
        "AV": "Y",
        "GP": "E",
        "UT": "1",
        "TP": "L",
        "CD": "042",
    }
    await wait_until(lambda: hass.states.get(FAULT).state == STATE_ON)
    attributes = hass.states.get(FAULT).attributes
    assert attributes["appliance"] == "evaporative_cooler"
    assert attributes["severity"] == "lockout"
    assert attributes["code"] == "042"


async def test_controller_temperature(
    hass: HomeAssistant, setup_integration: MockConfigEntry, module: FakeModule
) -> None:
    """The controller's MT (tenths of a degree) is the current temperature."""
    module.status["ECOM"]["GSS"]["MT"] = "245"
    await wait_until(
        lambda: hass.states.get(CLIMATE).attributes[ATTR_CURRENT_TEMPERATURE] == 24.5
    )


async def test_external_temperature_sensor(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A sensor picked in the options replaces the controller reading."""
    hass.states.async_set("sensor.hall", "21.5")
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_TEMPERATURE_SENSOR: "sensor.hall"}
    )
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes[ATTR_CURRENT_TEMPERATURE] == 21.5

    hass.states.async_set("sensor.hall", "23.0")
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes[ATTR_CURRENT_TEMPERATURE] == 23.0

    hass.states.async_set("sensor.hall", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes[ATTR_CURRENT_TEMPERATURE] is None
