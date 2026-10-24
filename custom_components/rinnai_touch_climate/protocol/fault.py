"""Fault report (SYST.FLT), as decoded by the Homebridge plugin."""

from __future__ import annotations

from dataclasses import dataclass

from .status import SYSTEM, Status

APPLIANCES = {
    "H": "heater",
    "C": "add_on_cooler",
    "E": "evaporative_cooler",
    "R": "reverse_cycle",
    "N": "controller",
}
SEVERITIES = {"M": "minor", "B": "busy", "L": "lockout"}


@dataclass(frozen=True, slots=True)
class Fault:
    """The fault the controller reports, if any."""

    detected: bool
    appliance: str | None
    unit: str | None
    severity: str | None
    code: str | None

    @classmethod
    def from_status(cls, status: Status) -> Fault | None:
        """Return the fault state, or None before the module reported it."""
        available = status.get(SYSTEM, "FLT", "AV")
        if available is None:
            return None
        if available != "Y":
            return cls(False, None, None, None, None)
        appliance = status.get(SYSTEM, "FLT", "GP")
        severity = status.get(SYSTEM, "FLT", "TP")
        return cls(
            detected=True,
            appliance=APPLIANCES.get(appliance or "", appliance),
            unit=status.get(SYSTEM, "FLT", "UT"),
            severity=SEVERITIES.get(severity or "", severity),
            code=status.get(SYSTEM, "FLT", "CD"),
        )
