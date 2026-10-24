"""The module's status document and command payloads.

A status frame is a JSON array of single-key objects, e.g.::

    [{"SYST": {"CFG": {...}, "AVM": {...}, "OSS": {...}, "FLT": {...}}},
     {"ECOM": {"CFG": {...}, "GSO": {...}, "GSS": {...}}}]

The first key ("group 1") is SYST or the unit in use (ECOM = evaporative
cooling, HGOM = gas heating, CGOM = add-on cooling). Below it are groups
like GSO (operation settings) and GSS (operation status), and below those
the two-letter values. A command uses the same nesting, e.g.
``{"ECOM": {"GSO": {"SW": "N"}}}``.
"""

from __future__ import annotations

from typing import Any

type States = dict[str, dict[str, dict[str, str]]]

SYSTEM = "SYST"
UNIT_EVAP = "ECOM"
UNIT_HEATER = "HGOM"
UNIT_COOLER = "CGOM"

# SYST.OSS.MD (operating mode) -> group 1 of the unit in use.
MODE_UNITS = {"E": UNIT_EVAP, "H": UNIT_HEATER, "C": UNIT_COOLER}


class Status:
    """The latest known value of every key the module has reported."""

    def __init__(self) -> None:
        """Start empty; nothing is known until the first frame."""
        self._data: dict[str, dict[str, Any]] = {}

    def update(self, frame_status: list[dict[str, Any]]) -> None:
        """Merge a received status array (each group 1 is replaced whole)."""
        for item in frame_status:
            for group1, value in item.items():
                if isinstance(value, dict):
                    self._data[group1] = value

    def get(self, group1: str, group2: str, key: str) -> str | None:
        """Return one value, or None if the module has not reported it."""
        value = self._data.get(group1, {}).get(group2, {}).get(key)
        return value if isinstance(value, str) else None

    def has_states(self, states: States) -> bool:
        """Return True if every value in a command payload is already set."""
        return all(
            self.get(group1, group2, key) == value
            for group1, groups in states.items()
            for group2, values in groups.items()
            for key, value in values.items()
        )

    @property
    def unit(self) -> str | None:
        """Return group 1 of the unit selected by the operating mode."""
        return MODE_UNITS.get(self.get(SYSTEM, "OSS", "MD") or "")

    @property
    def has_evap(self) -> bool:
        """Return True if an evaporative cooler is installed."""
        return self.get(SYSTEM, "AVM", "EC") == "Y"

    @property
    def firmware(self) -> str | None:
        """Return the module firmware version."""
        return self.get(SYSTEM, "CFG", "VR")

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the raw status, for diagnostics."""
        return {key: dict(value) for key, value in self._data.items()}


def state(group1: str, group2: str, key: str, value: str) -> States:
    """Build a single-value command payload."""
    return {group1: {group2: {key: value}}}


def set_operating_mode(mode: str) -> States:
    """Select the unit: E (evaporative), H (heating) or C (cooling)."""
    return state(SYSTEM, "OSS", "MD", mode)
