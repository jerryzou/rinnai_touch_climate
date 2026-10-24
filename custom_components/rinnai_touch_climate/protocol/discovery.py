"""The UDP gate: wait for the module to say its TCP slot is free.

While no TCP client is connected, the module broadcasts a 256-byte packet
to UDP port 50000 about once a second. It starts that about 7 s after a
client disconnects and stops the moment a client connects. So a broadcast
means "connect now", and connecting without one gets refused.

The packet starts with ``Rinnai_NBW2_Module``; bytes 32-33 hold the TCP
port as a big-endian uint16 (27847).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import socket

_LOGGER = logging.getLogger(__name__)

UDP_PORT = 50000
DEFAULT_TCP_PORT = 27847
MAGIC = b"Rinnai_NBW2_Module"


@dataclass(frozen=True, slots=True)
class ModuleAddress:
    """Where a broadcasting module accepts TCP connections."""

    host: str
    port: int


def parse_broadcast(data: bytes, host: str) -> ModuleAddress | None:
    """Return the module address if this packet is a module broadcast."""
    if not data.startswith(MAGIC):
        return None
    port = int.from_bytes(data[32:34], "big") if len(data) >= 34 else 0
    return ModuleAddress(host, port or DEFAULT_TCP_PORT)


def _bind(port: int) -> socket.socket:
    """Open a UDP socket that can share the port with other listeners."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setblocking(False)
        sock.bind(("", port))
    except OSError:
        sock.close()
        raise
    return sock


class _Listener(asyncio.DatagramProtocol):
    def __init__(self, on_packet) -> None:
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if (module := parse_broadcast(data, addr[0])) is not None:
            self._on_packet(module)


async def _listen(port: int, timeout: float, on_packet, done: asyncio.Future) -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Listener(on_packet), sock=_bind(port)
    )
    try:
        async with asyncio.timeout(timeout):
            await done
    finally:
        transport.close()


async def wait_for_module(
    host: str, timeout: float, port: int = UDP_PORT
) -> ModuleAddress:
    """Wait for a broadcast from ``host`` (an IP address).

    Raises TimeoutError when none arrives in time: another client holds
    the slot, the module is still releasing it, or it is offline.
    """
    loop = asyncio.get_running_loop()
    found: asyncio.Future[ModuleAddress] = loop.create_future()
    others: set[str] = set()

    def on_packet(module: ModuleAddress) -> None:
        if module.host == host:
            if not found.done():
                found.set_result(module)
        elif module.host not in others:
            others.add(module.host)
            _LOGGER.warning(
                "A Rinnai Touch module is broadcasting from %s, but this entry "
                "is set up for %s; has the module's IP address changed?",
                module.host,
                host,
            )

    await _listen(port, timeout, on_packet, found)
    return found.result()


async def discover(timeout: float, port: int = UDP_PORT) -> list[ModuleAddress]:
    """Return every module heard broadcasting within ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    modules: dict[str, ModuleAddress] = {}
    never: asyncio.Future[None] = loop.create_future()
    try:
        await _listen(port, timeout, lambda m: modules.setdefault(m.host, m), never)
    except TimeoutError:
        pass
    finally:
        never.cancel()
    return list(modules.values())
