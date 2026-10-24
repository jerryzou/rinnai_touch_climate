"""A fake Rinnai Touch WiFi module (N-BW2) that behaves like the real one.

Measured on a real module (2026-10-24) and reproduced here:

* It accepts one TCP client. The fake accepts and immediately closes any
  other connection and counts it in ``rejected``.
* It broadcasts ``Rinnai_NBW2_Module`` (256 bytes, TCP port at bytes 32-33)
  to UDP 50000 about once a second, only while no client is connected,
  starting about 7 s after a client disconnects (``release_delay``).
* It greets a client with ``*HELLO*`` and then streams ``N`` + sequence +
  JSON status, which TCP may split; the fake splits every frame.
* A command ``N`` + sequence + JSON changes the status, which then carries
  that sequence number. A bare ``N`` + sequence is a keepalive.

``go_silent()`` keeps the connection open but stops sending, the failure
that a 5 s idle timeout catches.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from typing import Any

# A real status frame body, as logged in funtastix/rinnaitouch issue #57:
# evaporative cooler in Manual mode, fan speed 2, pump on.
REAL_STATUS_TEXT = (
    '[{"SYST": {"CFG": {"MTSP": "N", "NC": "00", "DF": "N", "TU": "C", '
    '"CF": "1", "VR": "0183", "CV": "0010", "CC": "043", '
    '"ZA": "                ", "ZB": "                ", '
    '"ZC": "                ", "ZD": "                " }, '
    '"AVM": {"HG": "Y", "EC": "Y", "CG": "N", "RA": "N", "RH": "N", '
    '"RC": "N" }, "OSS": {"DY": "MON", "TM": "14:16", "BP": "Y", '
    '"RG": "Y", "ST": "N", "MD": "E", "DE": "N", "DU": "N", "AT": "999", '
    '"LO": "N" }, "FLT": {"AV": "N", '
    '"C3": "000" } } },{"ECOM": {"CFG": {"ZUIS": "Y", "ZAIS": "N", '
    '"ZBIS": "N", "ZCIS": "N", "ZDIS": "N", "TP": "N" }, '
    '"GSO": {"SW": "N", "OP": "M", "FS": "N", "PS": "N", "FL": "02", '
    '"ZUUE": "Y", "ZAUE": "N", "ZBUE": "N", "ZCUE": "N", "ZDUE": "N" }, '
    '"GSS": {"PW": "N", "BY": "Y", "PO": "N", "FO": "N", "SN": "N", '
    '"MT": "999", "ZUAE": "Y", "ZAAE": "N", "ZBAE": "N", "ZCAE": "N", '
    '"ZDAE": "N" }, "PSU": {"AV": "N", "SFE": "N" } } }]'
)
REAL_STATUS: dict[str, Any] = {
    key: value for item in json.loads(REAL_STATUS_TEXT) for key, value in item.items()
}


_COMMAND = re.compile(rb"N(\d{6})")
_DECODER = json.JSONDecoder()


def broadcast_packet(tcp_port: int) -> bytes:
    """Build the 256-byte UDP announcement."""
    packet = bytearray(b"Rinnai_NBW2_Module".ljust(32, b"\0"))
    packet += tcp_port.to_bytes(2, "big")
    return bytes(packet.ljust(256, b"\0"))


class FakeModule:
    """An asyncio TCP server plus UDP broadcaster on localhost."""

    def __init__(
        self,
        udp_port: int,
        *,
        release_delay: float = 0.3,
        broadcast_interval: float = 0.05,
        status_interval: float = 0.1,
        segments: int = 3,
        segment_delay: float = 0.005,
        first_status_delay: float = 0.0,
        apply_delay: float = 0.02,
        hello: bool = True,
        garbage: bool = False,
    ) -> None:
        """Configure the fake; call ``start()`` to run it."""
        self.udp_port = udp_port
        self.release_delay = release_delay
        self.broadcast_interval = broadcast_interval
        self.status_interval = status_interval
        self.segments = segments
        self.segment_delay = segment_delay
        self.first_status_delay = first_status_delay
        self.apply_delay = apply_delay
        self.hello = hello
        self.garbage = garbage

        self.status = copy.deepcopy(REAL_STATUS)
        self.sequence = 0
        self.port = 0
        self.ignore_commands = 0
        self.silent = False

        self.broadcasts = 0
        self.connections = 0
        self.disconnects = 0
        self.rejected = 0
        self.commands: list[tuple[int, dict[str, Any]]] = []
        self.keepalives: list[int] = []
        self.received: list[bytes] = []

        self._client: asyncio.StreamWriter | None = None
        self._free_at = 0.0
        self._server: asyncio.Server | None = None
        self._udp: asyncio.DatagramTransport | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Return True while a client holds the slot."""
        return self._client is not None

    @property
    def free(self) -> bool:
        """Return True when the slot is free and released."""
        return (
            self._client is None and asyncio.get_running_loop().time() >= self._free_at
        )

    async def start(self) -> None:
        """Listen on TCP and start broadcasting."""
        loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        self._udp, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=("127.0.0.1", self.udp_port)
        )
        self._spawn(self._broadcast())

    async def stop(self) -> None:
        """Shut everything down."""
        if self._client is not None:
            self._client.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._udp is not None:
            self._udp.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def go_silent(self) -> None:
        """Stop sending until the client disconnects."""
        self.silent = True

    def disconnect_client(self) -> None:
        """Drop the current client, as a module reboot would."""
        if self._client is not None:
            self._client.close()

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _broadcast(self) -> None:
        packet = broadcast_packet(self.port)
        while True:
            if self.free and self._udp is not None:
                self._udp.sendto(packet)
                self.broadcasts += 1
            await asyncio.sleep(self.broadcast_interval)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if not self.free:
            self.rejected += 1
            writer.close()
            return
        self._client = writer
        self.connections += 1
        streamer = self._spawn(self._stream(writer))
        buffer = bytearray()
        try:
            while data := await reader.read(4096):
                self.received.append(data)
                buffer += data
                self._parse(buffer, writer)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            streamer.cancel()
            self._client = None
            self.silent = False
            self.disconnects += 1
            self._free_at = asyncio.get_running_loop().time() + self.release_delay
            writer.close()

    def _parse(self, buffer: bytearray, writer: asyncio.StreamWriter) -> None:
        while (match := _COMMAND.search(buffer)) is not None:
            sequence = int(match.group(1))
            end = match.end()
            if end < len(buffer) and buffer[end : end + 1] == b"{":
                try:
                    command, end = _DECODER.raw_decode(buffer.decode(), end)
                except json.JSONDecodeError:
                    return  # incomplete
                del buffer[:end]
                self.commands.append((sequence, command))
                if self.ignore_commands:
                    self.ignore_commands -= 1
                    continue
                self._spawn(self._apply(sequence, command, writer))
            else:
                # A bare sequence number is a keepalive.
                del buffer[:end]
                self.keepalives.append(sequence)

    async def _apply(
        self, sequence: int, command: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        await asyncio.sleep(self.apply_delay)
        for group1, groups in command.items():
            for group2, values in groups.items():
                self.status.setdefault(group1, {}).setdefault(group2, {}).update(values)
        self.sequence = sequence
        if not self.silent:
            await self._send_status(writer)

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        if self.hello:
            writer.write(b"*HELLO*")
        await asyncio.sleep(self.first_status_delay)
        while True:
            if not self.silent:
                await self._send_status(writer)
            await asyncio.sleep(self.status_interval)

    async def _send_status(self, writer: asyncio.StreamWriter) -> None:
        body = json.dumps([{key: value} for key, value in self.status.items()])
        frame = f"N{self.sequence:06d}{body}".encode()
        if self.garbage:
            frame = b"\x00\xff garbage N12" + frame
        size = -(-len(frame) // self.segments)
        async with self._send_lock:
            try:
                for start in range(0, len(frame), size):
                    writer.write(frame[start : start + size])
                    await writer.drain()
                    await asyncio.sleep(self.segment_delay)
            except ConnectionError:
                pass
