"""Protocol client for the Rinnai/Brivis Touch WiFi module (N-BW2).

Plain asyncio with no Home Assistant imports, so it can be tested (and
reused) on its own.
"""

from .client import (
    CommandFailedError,
    ConnectionState,
    NotConnectedError,
    RinnaiTouchClient,
    RinnaiTouchError,
    Timing,
)
from .discovery import ModuleAddress, discover
from .fault import Fault
from .status import Status

__all__ = [
    "CommandFailedError",
    "ConnectionState",
    "Fault",
    "ModuleAddress",
    "NotConnectedError",
    "RinnaiTouchClient",
    "RinnaiTouchError",
    "Status",
    "Timing",
    "discover",
]
