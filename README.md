# Rinnai Touch Climate for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/jerryzou/rinnai_touch_climate/actions/workflows/validate.yml/badge.svg)](https://github.com/jerryzou/rinnai_touch_climate/actions/workflows/validate.yml)
[![Tests](https://github.com/jerryzou/rinnai_touch_climate/actions/workflows/tests.yml/badge.svg)](https://github.com/jerryzou/rinnai_touch_climate/actions/workflows/tests.yml)

A local integration for the **Rinnai / Brivis Touch WiFi module (N-BW2)**, the box that puts a Rinnai Touch wall controller on your network. It talks to the module directly over your LAN: no cloud, no Homebridge, no extra Python packages.

Evaporative coolers are supported for now. The code is laid out so gas heating and add-on cooling can be added later.

- A `climate` entity: **off**, **cool** (the cooler's Auto mode, with the comfort level as target temperature) and **fan only** (Manual mode, with fan speed 1–16 as fan mode).
- A **Pump** switch, a **Fault** problem sensor and a **Connection** diagnostic sensor.
- Optional: any temperature sensor in Home Assistant as the current temperature.
- A connection that survives restarts and reloads, which is the reason this integration exists (see [below](#why-another-rinnai-touch-integration)).

Not affiliated with Rinnai or Brivis.

This started in 2020 as a small YAML `climate` platform. Its ideas fed into the popular [rinnaitouch](https://github.com/funtastix/rinnaitouch) integration, which supports far more systems than this one. Version 1.0 is a rewrite by **Jerry Z & Claude** (Anthropic's AI, working in Claude Code), built around how the module actually behaves on the network.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/jerryzou/rinnai_touch_climate`, category **Integration**.
2. Install **Rinnai Touch Climate**, then restart Home Assistant.

### Manual

Copy `custom_components/rinnai_touch_climate` into your `config/custom_components/` folder, then restart Home Assistant.

### Upgrading from the 2020 YAML version

The `climate: - platform: rinnai_touch_climate` configuration is no longer read. Remove it from `configuration.yaml` and add the integration from the UI as below.

## Setup

**The module accepts only one connection at a time.** Stop anything else that talks to it first: Homebridge with a Rinnai Touch plugin, another Home Assistant integration (such as `rinnaitouch`), or the Rinnai app on a phone on the same network.

Then go to **Settings → Devices & services → Add integration → Rinnai Touch Climate**. The integration listens for the module for a few seconds and fills in its IP address. If nothing is found (usually because another client is still connected), type the address in. Give the module a fixed IP address in your router.

Home Assistant uses UDP port 50000 to hear the module. With HA OS, or Docker with `network_mode: host`, this works as is.

### Options

**Configure** on the integration lets you pick a temperature sensor for the climate entity's current temperature. Without one, the controller's own reading is used if it reports one (many evaporative systems don't).

## Entities

| Entity | What it does |
| --- | --- |
| `climate.rinnai_touch` | `off`; `cool` = Auto, target temperature = comfort level 19–34 (sent 1:1); `fan_only` = Manual, fan mode = speed 1–16. |
| `switch.rinnai_touch_pump` | The water pump. Turning it on switches the cooler to Manual, as the wall controller does. |
| `binary_sensor.rinnai_touch_fault` | On while the controller reports a fault. The appliance, unit, severity and fault code are attributes. |
| `sensor.rinnai_touch_connection` | `connected`, `connecting`, `waiting_for_module` or `disconnected`. |

All except the connection sensor are unavailable while the module is not connected.

A comfort level set while the cooler is off or in Manual is kept and sent the next time you choose `cool`. A fan speed set outside Manual waits for `fan_only` the same way. `climate.turn_on` goes back to the mode the cooler last ran in.

## How the connection works

The module is a small embedded device with a single TCP slot, and it is unforgiving about how that slot is used. Its behaviour, measured on a real module:

- While **no client is connected** it broadcasts a 256-byte packet beginning `Rinnai_NBW2_Module` to UDP port 50000, about once a second. Bytes 32–33 hold its TCP port (27847).
- The broadcasts **stop the moment a client connects**.
- After a client disconnects, the slot takes **about 7 seconds** to free up. Only then do the broadcasts resume. A connection attempt in that window is refused.
- Once connected, it streams its status (`N` + 6-digit sequence number + a JSON array) more often than every 5 seconds. A silent connection is a dead one.

So this integration treats the broadcast as a gate:

1. **Before every connect**, on the first connection and on every reconnect, it waits for a broadcast from the module's IP. A broadcast means the slot is free. Without one it never tries to connect, so it can't be refused and it never fights another client.
2. It counts as **connected only after the first valid status message**, not when TCP connects. A `*HELLO*` greeting is tolerated.
3. If **no byte arrives for 5 seconds**, it closes the connection and goes back to step 1. A failed TCP attempt is retried twice quickly, then after a minute. Waiting for the broadcast itself sends nothing, so it needs no back-off.
4. Incoming bytes are **buffered and parsed as complete messages** with a real JSON decoder. A message split across TCP segments waits for the rest, and junk is skipped.
5. **Commands go one at a time.** Each is numbered after the module's last sequence number (1–254, then back to 1), is skipped if the status already shows that value, and is confirmed against the status stream: up to 10 seconds, 3 tries. A bare sequence number goes out every 60 seconds as a keepalive.
6. It is plain asyncio, with no threads and nothing blocking the event loop. Unloading closes the socket and waits for it to close. A **reload** therefore simply waits out the ~7 s release instead of being refused.

This is the design of the [homebridge-rinnai-touch-platform](https://github.com/mantorok1/homebridge-rinnai-touch-platform) plugin, which has kept these modules connected for years. The status keys and command values come from that plugin and from [pyrinnaitouch](https://github.com/funtastix/pyrinnaitouch).

### Why another Rinnai Touch integration?

[rinnaitouch](https://github.com/funtastix/rinnaitouch) covers heaters, coolers, zones and multi-set-point systems, and you should use it if it works for you. On some networks, though, its connection keeps getting refused, especially after a Home Assistant restart or an integration reload. The causes are in the connection handling, not the climate logic: reconnecting straight after a disconnect lands in the ~7 s release window, and the connection is counted as up on the TCP handshake rather than on real status. This integration does only the evaporative cooler, and does the connection the way the module expects.

## Troubleshooting

- **Connection stuck at `waiting_for_module`**: another client holds the connection, or the module is offline. Stop the other client and Home Assistant connects within a few seconds. If nothing else is connected, power-cycle the module.
- **"A Rinnai Touch module is broadcasting from …"** in the log: the module's IP address changed. Give it a fixed address and re-add the integration.
- **Debug logging**: use **Enable debug logging** on the integration page, or add this to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.rinnai_touch_climate: debug
  ```

- **Diagnostics** on the device page download the raw status the module reports. Attach them to issues.

## Development

The protocol client in `custom_components/rinnai_touch_climate/protocol/` has no Home Assistant imports. The tests run it against a fake module (`tests/fake_module.py`) that copies the real one: a single client slot, broadcasts only while free, a release delay, status split across TCP segments and connections that go silent.

```bash
pip install -r requirements_test.txt
pytest
```

## License

MIT
