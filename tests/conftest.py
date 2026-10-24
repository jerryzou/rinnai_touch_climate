"""Fixtures for the Rinnai Touch Climate tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from functools import partial
import socket
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rinnai_touch_climate.const import DOMAIN
from custom_components.rinnai_touch_climate.protocol import RinnaiTouchClient, Timing

from .fake_module import FakeModule

# Real timings divided by about 20, so a reconnect takes well under a second.
FAST = Timing(
    broadcast_timeout=1.0,
    connect_timeout=1.0,
    first_status_timeout=1.0,
    idle_timeout=0.3,
    retry_delay=0.05,
    backoff_delay=0.3,
    reconnect_delay=0.05,
    keepalive_interval=0.2,
    command_timeout=0.5,
    close_timeout=0.5,
    settle_delay=0.2,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def udp_port(socket_enabled: None) -> int:
    """Return a free UDP port to stand in for 50000."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def make_module(udp_port: int) -> AsyncGenerator[Callable[..., FakeModule]]:
    """Return a factory for started fake modules; stops them afterwards."""
    modules: list[FakeModule] = []

    async def make(**kwargs) -> FakeModule:
        module = FakeModule(udp_port, **kwargs)
        await module.start()
        modules.append(module)
        return module

    yield make
    for module in modules:
        await module.stop()


@pytest.fixture
async def module(make_module) -> FakeModule:
    """Return a running fake module with default behaviour."""
    return await make_module()


async def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll until ``condition()`` is true (real sockets need real time)."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.fixture
def fast_client(udp_port: int) -> Generator[None]:
    """Make the integration use the test UDP port and fast timings."""
    with patch(
        "custom_components.rinnai_touch_climate.RinnaiTouchClient",
        partial(RinnaiTouchClient, udp_port=udp_port, timing=FAST),
    ):
        yield


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry for the fake module on localhost."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Rinnai Touch (127.0.0.1)", data={CONF_HOST: "127.0.0.1"}
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    module: FakeModule,
    fast_client: None,
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration and wait until the module is connected."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await wait_until(lambda: config_entry.runtime_data.connected)
    await hass.async_block_till_done()
    yield config_entry
    if config_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(config_entry.entry_id)
