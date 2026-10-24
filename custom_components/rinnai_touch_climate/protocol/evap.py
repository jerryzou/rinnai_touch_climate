"""Evaporative cooler (ECOM) values and commands.

Values and their meaning come from the Homebridge plugin
(mantorok1/homebridge-rinnai-touch-platform) and pyrinnaitouch:

GSO (settings): SW power N/F, OP control mode A (auto) / M (manual),
    SP comfort level 19-34 (auto), FL fan speed 01-16 (manual),
    FS fan N/F (manual), PS water pump N/F (manual).
GSS (status): BY busy, PW prewetting, MT measured temperature in 0.1 °C
    (999 = no sensor).
"""

from __future__ import annotations

from dataclasses import dataclass

from .status import UNIT_EVAP, States, Status, set_operating_mode, state

COMFORT_MIN = 19
COMFORT_MAX = 34
FAN_SPEED_MIN = 1
FAN_SPEED_MAX = 16

NO_TEMPERATURE = "999"


def _flag(value: str | None, on: str = "N") -> bool | None:
    return None if value is None else value == on


def _number(value: str | None) -> int | None:
    try:
        return None if value is None else int(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class EvapState:
    """What the evaporative cooler is doing, as last reported."""

    power: bool | None
    auto: bool | None
    comfort: int | None
    fan_speed: int | None
    fan: bool | None
    pump: bool | None
    busy: bool | None
    prewetting: bool | None
    temperature: float | None

    @classmethod
    def from_status(cls, status: Status) -> EvapState:
        """Read the ECOM values out of a status document."""

        def gso(key: str) -> str | None:
            return status.get(UNIT_EVAP, "GSO", key)

        def gss(key: str) -> str | None:
            return status.get(UNIT_EVAP, "GSS", key)

        measured = gss("MT")
        temperature = (
            None
            if measured in (None, NO_TEMPERATURE) or _number(measured) is None
            else int(measured) / 10
        )
        mode = gso("OP")
        return cls(
            power=_flag(gso("SW")) if status.unit == UNIT_EVAP else False,
            auto=None if mode is None else mode == "A",
            comfort=_number(gso("SP")),
            fan_speed=_number(gso("FL")),
            fan=_flag(gso("FS")),
            pump=_flag(gso("PS")),
            busy=_flag(gss("BY"), "Y"),
            prewetting=_flag(gss("PW"), "Y"),
            temperature=temperature,
        )


def _gso(key: str, value: str) -> States:
    return state(UNIT_EVAP, "GSO", key, value)


def power(on: bool) -> States:
    """Switch the cooler on or off."""
    return _gso("SW", "N" if on else "F")


def control_mode(auto: bool) -> States:
    """Select auto (comfort level) or manual (fan speed) control."""
    return _gso("OP", "A" if auto else "M")


def comfort(level: int) -> States:
    """Set the auto-mode comfort level."""
    if not COMFORT_MIN <= level <= COMFORT_MAX:
        raise ValueError(f"comfort level {level} is outside 19-34")
    return _gso("SP", f"{level:02d}")


def fan_speed(speed: int) -> States:
    """Set the manual-mode fan speed."""
    if not FAN_SPEED_MIN <= speed <= FAN_SPEED_MAX:
        raise ValueError(f"fan speed {speed} is outside 1-16")
    return _gso("FL", f"{speed:02d}")


def fan(on: bool) -> States:
    """Run or stop the fan in manual mode."""
    return _gso("FS", "N" if on else "F")


def pump(on: bool) -> States:
    """Run or stop the water pump in manual mode."""
    return _gso("PS", "N" if on else "F")


def turn_on_auto(level: int | None = None) -> list[States]:
    """Commands that leave the cooler running in auto mode."""
    commands = [set_operating_mode("E"), power(True), control_mode(auto=True)]
    if level is not None:
        commands.append(comfort(level))
    return commands


def turn_on_manual(speed: int | None = None) -> list[States]:
    """Commands that leave the cooler running its fan in manual mode."""
    commands = [
        set_operating_mode("E"),
        power(True),
        control_mode(auto=False),
        fan(True),
    ]
    if speed is not None:
        commands.append(fan_speed(speed))
    return commands


def set_pump(on: bool) -> list[States]:
    """Commands for the pump; turning it on needs manual mode."""
    if not on:
        return [pump(False)]
    return [set_operating_mode("E"), power(True), control_mode(auto=False), pump(True)]
