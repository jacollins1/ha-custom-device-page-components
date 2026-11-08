"""Websocket commands used by the device-page card.

Every command needs an administrator, the same as the device page itself.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from . import mmwave
from .actions import ActionRefused
from .const import CONF_MMWAVE, DOMAIN


def _runtime(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register commands."""
    websocket_api.async_register_command(hass, ws_device)
    websocket_api.async_register_command(hass, ws_run_action)
    websocket_api.async_register_command(hass, ws_attach_report)
    websocket_api.async_register_command(hass, ws_mmwave_command)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/device", vol.Required("device_id"): str}
)
@websocket_api.async_response
async def ws_device(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Components for one device page."""
    runtime = _runtime(hass)
    if runtime is None:
        connection.send_result(msg["id"], {"version": None, "components": []})
        return
    connection.send_result(msg["id"], await runtime.async_device_payload(msg["device_id"]))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/run_action",
        vol.Required("device_id"): str,
        vol.Required("component_id"): str,
        vol.Required("action_index"): int,
        vol.Required("row_key"): str,
        vol.Optional("confirmed", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_run_action(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Run a row action as the signed-in user."""
    runtime = _runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    try:
        await runtime.async_run_row_action(
            msg["device_id"],
            msg["component_id"],
            msg["action_index"],
            msg["row_key"],
            msg["confirmed"],
            connection.user.id,
        )
    except ActionRefused as err:
        connection.send_error(msg["id"], "refused", str(err))
        return
    except Exception as err:  # noqa: BLE001 - surface service errors
        connection.send_error(msg["id"], "action_failed", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/attach_report",
        vol.Required("ok"): bool,
        vol.Optional("reason"): vol.Any(str, None),
        vol.Optional("ha_version"): vol.Any(str, None),
        vol.Optional("module"): vol.Any(str, None),
    }
)
@callback
def ws_attach_report(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A browser tells us whether the card could attach."""
    if (runtime := _runtime(hass)) is not None:
        runtime.async_attach_report(
            msg["ok"], msg.get("reason"), msg.get("ha_version"), msg.get("module")
        )
    connection.send_result(msg["id"], {})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/mmwave_command",
        vol.Required("device_id"): str,
        vol.Required("command"): vol.In(["generate", "clear"]),
        vol.Required("confirmed"): bool,
    }
)
@websocket_api.async_response
async def ws_mmwave_command(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Inovelli mmWave: generate or clear the stored interference area."""
    if not msg["confirmed"]:
        connection.send_error(msg["id"], "not_allowed", "This command needs a confirmation")
        return
    runtime = _runtime(hass)
    if runtime is None or not runtime.entry.options.get(CONF_MMWAVE, True):
        connection.send_error(msg["id"], "not_allowed", "The mmWave component is turned off")
        return
    try:
        await mmwave.async_send(hass, msg["device_id"], msg["command"], connection.user.id)
    except ActionRefused as err:
        connection.send_error(msg["id"], "not_allowed", str(err))
        return
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "send_failed", str(err))
        return
    connection.send_result(msg["id"], {"sent": True})

