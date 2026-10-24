"""Tests for the protocol client against the fake module."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
import json

import pytest

from custom_components.rinnai_touch_climate.protocol import (
    CommandFailedError,
    ConnectionState,
    NotConnectedError,
    RinnaiTouchClient,
    evap,
)
from custom_components.rinnai_touch_climate.protocol.client import next_sequence
from custom_components.rinnai_touch_climate.protocol.discovery import parse_broadcast

from .conftest import FAST, wait_until
from .fake_module import FakeModule, broadcast_packet


@pytest.fixture
async def make_client(
    udp_port: int,
) -> AsyncGenerator[Callable[[], RinnaiTouchClient]]:
    """Return a factory for started clients; stops them afterwards."""
    clients: list[RinnaiTouchClient] = []

    def make() -> RinnaiTouchClient:
        client = RinnaiTouchClient("127.0.0.1", udp_port=udp_port, timing=FAST)
        client.start()
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.stop()


@pytest.fixture
async def client(module: FakeModule, make_client) -> RinnaiTouchClient:
    """Return a client connected to the default fake module."""
    client = make_client()
    await wait_until(lambda: client.connected)
    return client


def test_next_sequence() -> None:
    """Sequence numbers run 1-254 and never 0, like the plugin's."""
    assert next_sequence(0) == 1
    assert next_sequence(7) == 8
    assert next_sequence(253) == 254
    assert next_sequence(254) == 1
    assert next_sequence(255) == 1


def test_parse_broadcast() -> None:
    """The TCP port comes from bytes 32-33 of the announcement."""
    module = parse_broadcast(broadcast_packet(27847), "192.168.1.9")
    assert module is not None
    assert (module.host, module.port) == ("192.168.1.9", 27847)
    assert parse_broadcast(b"something else", "192.168.1.9") is None
    short = parse_broadcast(b"Rinnai_NBW2_Module", "192.168.1.9")
    assert short is not None and short.port == 27847


async def test_connects_after_broadcast_and_first_status(
    make_module, make_client
) -> None:
    """Connected only after a broadcast and then the first status frame."""
    module = await make_module(first_status_delay=0.15)
    client = make_client()
    states: list[ConnectionState] = []
    client.add_listener(lambda: states.append(client.state))

    await wait_until(lambda: module.connected)
    assert module.broadcasts > 0
    # The TCP handshake and *HELLO* are not enough.
    assert client.state is ConnectionState.CONNECTING
    await wait_until(lambda: client.connected)

    assert states[:3] == [
        ConnectionState.WAITING,
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
    ]
    assert client.status.has_evap
    assert evap.EvapState.from_status(client.status).fan_speed == 2
    assert module.rejected == 0


async def test_waits_while_another_client_holds_the_slot(
    module: FakeModule, make_client
) -> None:
    """With the slot taken there are no broadcasts, so no connect attempts."""
    _, other = await asyncio.open_connection("127.0.0.1", module.port)
    await wait_until(lambda: module.connected)

    client = make_client()
    await asyncio.sleep(FAST.broadcast_timeout * 1.5)
    assert client.state is ConnectionState.WAITING
    assert module.connections == 1
    assert module.rejected == 0

    other.close()
    await other.wait_closed()
    await wait_until(lambda: client.connected)
    assert module.connections == 2
    assert module.rejected == 0


async def test_reconnects_after_silence(
    client: RinnaiTouchClient, module: FakeModule
) -> None:
    """No bytes for the idle timeout: close, wait for the slot, reconnect."""
    module.go_silent()
    await wait_until(lambda: not client.connected)
    assert client.last_error == f"no data for {FAST.idle_timeout:g} s"
    await wait_until(lambda: module.disconnects == 1)

    await wait_until(lambda: client.connected)
    assert client.connection_count == 2
    assert module.connections == 2
    # The release delay was waited out, never hit.
    assert module.rejected == 0


async def test_release_slower_than_broadcast_timeout(make_module, make_client) -> None:
    """A slot that takes longer to free than one UDP wait is still waited out."""
    module = await make_module(release_delay=FAST.broadcast_timeout * 1.5)
    client = make_client()
    await wait_until(lambda: client.connected)
    module.disconnect_client()
    await wait_until(lambda: client.connection_count == 2 and client.connected)
    assert module.rejected == 0


async def test_reconnects_after_module_drops_client(
    client: RinnaiTouchClient, module: FakeModule
) -> None:
    """A closed connection is reopened through the UDP gate."""
    module.disconnect_client()
    await wait_until(lambda: client.connection_count == 2 and client.connected)
    assert module.rejected == 0


async def test_new_client_waits_after_a_close(
    client: RinnaiTouchClient, module: FakeModule, make_client
) -> None:
    """A reload's new client waits out the settle delay after the old close.

    The real module refuses an address that reconnects within about a
    second, although it is already broadcasting again.
    """
    await client.stop()
    loop = asyncio.get_running_loop()
    closed = loop.time()
    second = make_client()
    await wait_until(lambda: second.connected)
    assert loop.time() - closed >= FAST.settle_delay * 0.9
    assert module.rejected == 0


async def test_split_frames_and_garbage(make_module, make_client) -> None:
    """Status split into many segments with junk in between still parses."""
    module = await make_module(segments=11, garbage=True)
    client = make_client()
    await wait_until(lambda: client.connected)
    await asyncio.sleep(FAST.idle_timeout * 2)
    assert client.connected
    assert client.connection_count == 1
    assert client.status.unit == "ECOM"
    assert module.rejected == 0


async def test_command_confirmed(client: RinnaiTouchClient, module: FakeModule) -> None:
    """A command carries the next sequence number and waits for the status."""
    module.sequence = 41
    await wait_until(lambda: client._last_sequence == 41)

    await client.async_send(evap.fan_speed(5))

    assert module.commands == [(42, {"ECOM": {"GSO": {"FL": "05"}}})]
    assert b'N000042{"ECOM":{"GSO":{"FL":"05"}}}' in b"".join(module.received)
    assert evap.EvapState.from_status(client.status).fan_speed == 5


async def test_command_sequence(client: RinnaiTouchClient, module: FakeModule) -> None:
    """Several commands run in order, skipping those already in effect."""
    await client.async_send(*evap.turn_on_auto(27))

    # MD=E, SW=N were already set; OP and SP were sent.
    assert [command for _, command in module.commands] == [
        {"ECOM": {"GSO": {"OP": "A"}}},
        {"ECOM": {"GSO": {"SP": "27"}}},
    ]
    assert [seq for seq, _ in module.commands] == [1, 2]
    state = evap.EvapState.from_status(client.status)
    assert (state.auto, state.comfort) == (True, 27)


async def test_command_skipped_when_already_set(
    client: RinnaiTouchClient, module: FakeModule
) -> None:
    """Nothing is sent when the status already shows the value."""
    await client.async_send(evap.fan_speed(2))
    assert module.commands == []


async def test_command_retried(client: RinnaiTouchClient, module: FakeModule) -> None:
    """An unconfirmed command is resent."""
    module.ignore_commands = 1
    await client.async_send(evap.pump(False))
    assert len(module.commands) == 2
    assert module.status["ECOM"]["GSO"]["PS"] == "F"


async def test_command_fails_after_three_tries(
    client: RinnaiTouchClient, module: FakeModule
) -> None:
    """Three unconfirmed tries raise."""
    module.ignore_commands = 3
    with pytest.raises(CommandFailedError):
        await client.async_send(evap.pump(False))
    assert len(module.commands) == 3


async def test_sequence_wraps(client: RinnaiTouchClient, module: FakeModule) -> None:
    """After 254 the next sequence number is 1, not 255 or 0."""
    module.sequence = 254
    await wait_until(lambda: client._last_sequence == 254)
    await client.async_send(evap.fan_speed(9))
    assert module.commands[-1][0] == 1

    module.sequence = 253
    await wait_until(lambda: client._last_sequence == 253)
    await client.async_send(evap.fan_speed(10))
    assert module.commands[-1][0] == 254


async def test_keepalive(client: RinnaiTouchClient, module: FakeModule) -> None:
    """A bare sequence number is sent every keepalive interval."""
    module.sequence = 12
    await wait_until(lambda: len(module.keepalives) >= 2)
    assert set(module.keepalives) <= {1, 13}
    assert module.commands == []


async def test_not_connected(make_client, module: FakeModule) -> None:
    """Commands fail fast while not connected."""
    module.go_silent()
    client = make_client()
    with pytest.raises(NotConnectedError):
        await client.async_send(evap.power(False))


async def test_stop_closes_and_restart_waits_for_release(
    client: RinnaiTouchClient, module: FakeModule, make_client
) -> None:
    """stop() closes the socket; a new client waits out the release delay."""
    await client.stop()
    assert client.state is ConnectionState.DISCONNECTED
    await wait_until(lambda: not module.connected)

    second = make_client()
    await wait_until(lambda: second.connected)
    assert module.connections == 2
    assert module.rejected == 0


async def test_stop_while_waiting(make_client, udp_port: int) -> None:
    """stop() returns promptly while waiting for a broadcast."""
    client = make_client()
    await wait_until(lambda: client.state is ConnectionState.WAITING)
    async with asyncio.timeout(1):
        await client.stop()


async def test_statuses_deduplicated(
    client: RinnaiTouchClient, module: FakeModule
) -> None:
    """Listeners hear about changes, not every repeated frame."""
    calls: list[str] = []
    client.add_listener(lambda: calls.append(json.dumps(client.status.as_dict())))
    await asyncio.sleep(FAST.idle_timeout)
    assert calls == []
    await client.async_send(evap.fan_speed(3))
    assert len(calls) == 1
