"""Asyncio client for the Rinnai/Brivis Touch WiFi module (N-BW2).

The connection behaviour copies the Homebridge plugin
mantorok1/homebridge-rinnai-touch-platform, which keeps this module
connected for weeks at a time:

* Connect only after the module's UDP broadcast says its single TCP slot
  is free (see ``discovery``), on the first connect and on every reconnect.
* Count as connected from the first valid status frame, not the handshake.
* Drop and reconnect when no byte arrives for 5 s; the module streams
  status more often than that. A failed TCP attempt is retried twice
  quickly, then after a minute. Listening for the broadcast sends nothing
  to the module, so it goes on without a back-off.
* Send one command at a time, numbered after the module's last sequence
  number, and confirm it against the status stream (10 s, up to 3 tries).
* Send a bare sequence number as a keepalive every 60 s.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from enum import StrEnum
import ipaddress
import json
import logging
import socket

from .discovery import UDP_PORT, ModuleAddress, wait_for_module
from .framing import Frame, FrameParser
from .status import States, Status

_LOGGER = logging.getLogger(__name__)


class ConnectionState(StrEnum):
    """Where the client is in its connection cycle."""

    DISCONNECTED = "disconnected"
    WAITING = "waiting_for_module"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class RinnaiTouchError(Exception):
    """Base class for client errors."""


class NotConnectedError(RinnaiTouchError):
    """The module is not connected, so nothing can be sent."""


class CommandFailedError(RinnaiTouchError):
    """The module did not confirm a command."""


@dataclass(frozen=True, slots=True)
class Timing:
    """Timeouts and delays in seconds; tests shrink them."""

    broadcast_timeout: float = 10.0
    connect_timeout: float = 5.0
    first_status_timeout: float = 10.0
    idle_timeout: float = 5.0
    connect_attempts: int = 3
    retry_delay: float = 0.5
    backoff_delay: float = 60.0
    reconnect_delay: float = 2.0
    keepalive_interval: float = 60.0
    command_timeout: float = 10.0
    command_attempts: int = 3
    close_timeout: float = 2.0


def next_sequence(last: int) -> int:
    """Return the sequence number after ``last`` (1-254, as the plugin does)."""
    return (last + 1) % 255 or 1


class RinnaiTouchClient:
    """Keeps one connection to the module and exposes its status."""

    def __init__(
        self, host: str, *, udp_port: int = UDP_PORT, timing: Timing | None = None
    ) -> None:
        """Set up the client; call ``start()`` to connect."""
        self.host = host
        self.status = Status()
        self.state = ConnectionState.DISCONNECTED
        self.address: ModuleAddress | None = None
        self.last_error: str | None = None
        self.connection_count = 0
        self._udp_port = udp_port
        self._timing = timing or Timing()
        self._listeners: list[Callable[[], None]] = []
        self._task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._last_sequence = 0
        self._last_raw: str | None = None
        self._command_lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._failing = False

    @property
    def connected(self) -> bool:
        """Return True once the module is streaming status to us."""
        return self.state is ConnectionState.CONNECTED

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` on every status or connection change."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def start(self) -> None:
        """Start connecting in the background."""
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name=f"rinnai_touch_climate {self.host}"
            )

    async def stop(self) -> None:
        """Close the connection and wait until it is closed."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise

    async def async_send(self, *commands: States) -> None:
        """Send commands in order, each confirmed before the next.

        A command whose values the status already shows is skipped.
        """
        async with self._command_lock:
            for states in commands:
                await self._send_confirmed(states)

    # Connection cycle

    async def _run(self) -> None:
        failures = 0
        while True:
            reached_connected = False
            try:
                self.address = await self._wait_for_slot()
                self._set_state(ConnectionState.CONNECTING)
                reached_connected = await self._session(self.address)
            except TimeoutError:
                self._failed("timed out connecting or waiting for the first status")
            except OSError as err:
                self._failed(str(err) or type(err).__name__)
            except Exception:
                _LOGGER.exception("Unexpected error in the Rinnai Touch connection")
                self._failing = True
            finally:
                self._set_state(ConnectionState.DISCONNECTED)

            if reached_connected:
                failures = 0
                delay = self._timing.reconnect_delay
            else:
                failures += 1
                if failures % self._timing.connect_attempts:
                    delay = self._timing.retry_delay
                else:
                    delay = self._timing.backoff_delay
                    _LOGGER.debug(
                        "%d connection attempts failed; next try in %.0f s",
                        failures,
                        delay,
                    )
            await asyncio.sleep(delay)

    async def _wait_for_slot(self) -> ModuleAddress:
        """Listen until the module broadcasts that its slot is free.

        Listening sends nothing to the module, so it needs no back-off and
        picks up a freed slot (e.g. Homebridge stopped) within a second.
        """
        host = await self._resolve()
        self._set_state(ConnectionState.WAITING)
        while True:
            try:
                return await wait_for_module(
                    host, self._timing.broadcast_timeout, self._udp_port
                )
            except TimeoutError:
                self._failed(
                    "the module is not broadcasting (another client, such as "
                    "Homebridge, may be connected, or it is offline)"
                )

    async def _resolve(self) -> str:
        """Return the module's IP address; broadcasts are matched on it."""
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            infos = await asyncio.get_running_loop().getaddrinfo(
                self.host, None, family=socket.AF_INET, type=socket.SOCK_STREAM
            )
            return str(infos[0][4][0])
        return self.host

    async def _session(self, address: ModuleAddress) -> bool:
        """Run one TCP connection; return True if it got to connected.

        Errors before the first status frame propagate, so the caller
        counts a failed attempt. Losing an established connection returns.
        """
        timing = self._timing
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(timing.connect_timeout):
            reader, writer = await asyncio.open_connection(address.host, address.port)
        self._writer = writer
        parser = FrameParser()
        keepalive: asyncio.Task[None] | None = None
        deadline = loop.time() + timing.first_status_timeout
        try:
            while True:
                timeout = timing.idle_timeout
                if keepalive is None:
                    timeout = min(timeout, deadline - loop.time())
                try:
                    async with asyncio.timeout(max(timeout, 0)):
                        data = await reader.read(4096)
                except TimeoutError:
                    if keepalive is None:
                        raise
                    self._lost(f"no data for {timing.idle_timeout:g} s")
                    return True
                except OSError as err:
                    if keepalive is None:
                        raise
                    self._lost(str(err) or type(err).__name__)
                    return True
                if not data:
                    if keepalive is None:
                        raise ConnectionError("the module closed the connection")
                    self._lost("the module closed the connection")
                    return True
                for frame in parser.feed(data):
                    changed = self._handle_frame(frame)
                    if keepalive is None:
                        keepalive = asyncio.create_task(self._keepalive())
                        self._connected()  # notifies listeners
                    elif changed:
                        self._notify()
        finally:
            self._writer = None
            if keepalive is not None:
                keepalive.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive
            writer.close()
            try:
                async with asyncio.timeout(timing.close_timeout):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass

    def _connected(self) -> None:
        self.connection_count += 1
        self.last_error = None
        if self._failing:
            _LOGGER.info("Reconnected to the Rinnai Touch module at %s", self.host)
        else:
            _LOGGER.debug("Connected to the Rinnai Touch module at %s", self.host)
        self._failing = False
        self._set_state(ConnectionState.CONNECTED)

    def _lost(self, reason: str) -> None:
        self.last_error = reason
        _LOGGER.warning(
            "Lost the connection to the Rinnai Touch module at %s: %s; reconnecting",
            self.host,
            reason,
        )
        self._failing = True

    def _failed(self, reason: str) -> None:
        self.last_error = reason
        if not self._failing:
            _LOGGER.warning(
                "Cannot connect to the Rinnai Touch module at %s: %s; will keep trying",
                self.host,
                reason,
            )
            self._failing = True
        else:
            _LOGGER.debug("Connection attempt to %s failed: %s", self.host, reason)

    def _set_state(self, state: ConnectionState) -> None:
        if state is not self.state:
            self.state = state
            self._notify()

    def _handle_frame(self, frame: Frame) -> bool:
        """Take in a frame; return True if the status changed."""
        self._last_sequence = frame.sequence
        if frame.raw == self._last_raw:
            return False
        self._last_raw = frame.raw
        self.status.update(frame.status)
        return True

    def _notify(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Error in a Rinnai Touch status listener")

    # Commands

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(self._timing.keepalive_interval)
            async with self._command_lock:
                try:
                    await self._write(f"N{next_sequence(self._last_sequence):06d}")
                except (NotConnectedError, OSError, TimeoutError) as err:
                    _LOGGER.debug("Keepalive not sent: %s", err)

    async def _write(self, payload: str) -> None:
        writer = self._writer
        if writer is None or not self.connected:
            raise NotConnectedError("not connected to the module")
        _LOGGER.debug("Sending %s", payload)
        writer.write(payload.encode())
        async with asyncio.timeout(self._timing.close_timeout):
            await writer.drain()

    async def _send_confirmed(self, states: States) -> None:
        payload = json.dumps(states, separators=(",", ":"))
        if self.status.has_states(states):
            _LOGGER.debug("Skipping %s: already in that state", payload)
            return
        for attempt in range(1, self._timing.command_attempts + 1):
            try:
                await self._write(f"N{next_sequence(self._last_sequence):06d}{payload}")
            except (OSError, TimeoutError) as err:
                raise NotConnectedError(f"sending failed: {err}") from err
            if await self._wait_for(states):
                return
            _LOGGER.warning(
                "The module did not confirm %s (attempt %d of %d)",
                payload,
                attempt,
                self._timing.command_attempts,
            )
        raise CommandFailedError(f"the module did not confirm {payload}")

    async def _wait_for(self, states: States) -> bool:
        try:
            async with asyncio.timeout(self._timing.command_timeout):
                while not self.status.has_states(states):
                    if not self.connected:
                        raise NotConnectedError("the connection was lost")
                    await self._changed.wait()
        except TimeoutError:
            return False
        return True
