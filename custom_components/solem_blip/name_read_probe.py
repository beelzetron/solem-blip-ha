"""Issue-#136 name-read probe, runnable through the integration's own BLE stack.

The standalone ``validate --probe-name-read`` requires a local Bluetooth
adapter; users whose controller talks through an ESPHome proxy can only
reach it via the Home Assistant Bluetooth manager. This module replays the
two discriminating shapes through the integration's client factory — the
same ``PersistentSolemClient`` class, the same ``ble_device_resolver``, the
same backend — so the result is diagnostic for the production code path:

- **Shape A** — station-name snapshot as the FIRST operation on a fresh
  connection (no status poll first).
- **Shape B** — status poll first, then (after ``delay`` seconds, on the
  same held link) the station-name snapshot.

A works while B fails  → the trigger is the preceding commit on the link.
Both fail              → the firmware refuses the ``35 00`` request in
every state on this firmware.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from solem_blip_ble.client_persistent import PersistentSolemClient

from .client_factory import build_solem_client
from .bluetooth import async_get_connectable_device
from .const import CONFIG_FLOW_CONNECT_RETRY_DELAY

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

# Single-connection controller: a connect that collides with the
# coordinator's own poll (or a link still being released) fails without
# retrying; the probe therefore mirrors the config-flow discipline of one
# delayed re-attempt before giving up.
_PROBE_CONNECT_RETRIES = 2
_PROBE_SETTLE_SECONDS = 20.0


async def _with_fresh_client(
    coordinator: SolemCoordinator, op_name: str, op: Any
) -> dict[str, Any]:
    """Run one operation on a fresh temporary client, retrying the connect.

    The temporary client shares the coordinator's resolver and options but
    is a separate instance, so shape A never reuses (or disturbs) the
    production link. Connect-phase failures get one delayed retry — the
    same treatment the config-flow validation read gets. Each attempt gets
    a brand-new client; the persistent variant is explicitly disconnected
    after the attempt so a held link can never leak into the next step
    (safe to call when already disconnected).
    """
    last_err: Exception | None = None
    for attempt in range(_PROBE_CONNECT_RETRIES):
        client = build_solem_client(
            coordinator.config_entry,
            mac_address=coordinator.controller_mac_address,
            bluetooth_timeout=coordinator.bluetooth_timeout,
            mock=coordinator.solem_api_mock,
            max_station_num=coordinator.num_stations,
            ble_device_resolver=lambda: async_get_connectable_device(
                coordinator.hass, coordinator.controller_mac_address
            ),
        )
        try:
            result: dict[str, Any] = await op(client)
            return result
        except Exception as err:  # noqa: BLE001 - probe reports, never raises
            last_err = err
            _LOGGER.debug(
                "%s - Probe %s attempt %d failed: %s",
                coordinator.controller_mac_address,
                op_name,
                attempt + 1,
                type(err).__name__,
            )
        finally:
            # Only the persistent variant holds a link between operations;
            # disconnect is safe to call when already disconnected. The
            # stateless client closes per operation and has no disconnect.
            if isinstance(client, PersistentSolemClient):
                await client.disconnect()
        await asyncio.sleep(CONFIG_FLOW_CONNECT_RETRY_DELAY)
    return {
        "ok": False,
        "error": f"{type(last_err).__name__}: {last_err}" if last_err else "unknown",
    }


async def run_name_read_probe(
    coordinator: SolemCoordinator, delay: int
) -> dict[str, Any]:
    """Execute both discriminating shapes and return the result matrix."""
    mac = coordinator.controller_mac_address

    async def _shape_a(client: Any) -> dict[str, Any]:
        started = asyncio.get_running_loop().time()
        snapshot = await client.get_station_name_snapshot()
        elapsed = asyncio.get_running_loop().time() - started
        return {
            "ok": True,
            "detail": (
                f"ok in {elapsed:.1f}s; {snapshot.reported_count} output(s) reported"
            ),
        }

    shape_a = await _with_fresh_client(coordinator, "shape A (name-first)", _shape_a)
    _LOGGER.info(
        "%s - Probe shape A (name-first): %s",
        mac,
        shape_a.get("detail") or shape_a.get("error"),
    )

    # Let the controller leave the post-disconnect quiet window before
    # shape B opens its own connection.
    await asyncio.sleep(_PROBE_SETTLE_SECONDS)

    async def _shape_b(client: Any) -> dict[str, Any]:
        started = asyncio.get_running_loop().time()
        status = await client.get_status()
        status_elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(delay)
        started = asyncio.get_running_loop().time()
        try:
            snapshot = await client.get_station_name_snapshot()
        except Exception as err:  # noqa: BLE001 - the shape verdict needs both halves
            return {
                "ok": True,
                "status_detail": (
                    f"ok in {status_elapsed:.1f}s "
                    f"(battery={status.get('battery_level')})"
                ),
                "name_error": f"{type(err).__name__}: {err}",
            }
        name_elapsed = asyncio.get_running_loop().time() - started
        return {
            "ok": True,
            "status_detail": (
                f"ok in {status_elapsed:.1f}s (battery={status.get('battery_level')})"
            ),
            "name_detail": (
                f"ok in {name_elapsed:.1f}s; "
                f"{snapshot.reported_count} output(s) reported"
            ),
        }

    shape_b = await _with_fresh_client(
        coordinator, "shape B (status-then-name)", _shape_b
    )
    _LOGGER.info(
        "%s - Probe shape B (status-then-name): %s / %s",
        mac,
        shape_b.get("status_detail") or shape_b.get("error"),
        shape_b.get("name_detail", "-") if shape_b.get("ok") else "-",
    )

    a_ok = shape_a.get("ok") is True
    b_status_ok = shape_b.get("ok") is True
    b_name_ok = b_status_ok and "name_detail" in shape_b
    if a_ok and b_status_ok and not b_name_ok:
        verdict = (
            "name-first works, status-then-name fails — the trigger is the "
            "preceding commit on the same link"
        )
    elif a_ok and b_name_ok:
        verdict = "both shapes worked in this run"
    elif not a_ok and b_status_ok and not b_name_ok:
        verdict = (
            "both shapes failed to read names — this firmware refuses the "
            "35 00 request in every state"
        )
    elif not a_ok and not b_status_ok:
        verdict = "connection failed before any shape could run — retry later"
    else:
        verdict = "mixed outcome — run the probe again before concluding"

    _LOGGER.info("%s - Probe verdict: %s", mac, verdict)
    return {
        "shape_a": shape_a,
        "shape_b": shape_b,
        "verdict": verdict,
    }
