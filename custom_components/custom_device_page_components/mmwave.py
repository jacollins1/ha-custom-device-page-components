"""Inovelli mmWave interference area: Generate / Clear from the device page.

Inovelli's mmWave presence switches can scan the room and store whatever is
moving at that moment (HVAC airflow, a fan, curtains) as an interference
area, which they then ignore. This module turns the two safe commands into
the exact Home Assistant action for the open device:

- VZM32-SN (ZHA): manufacturer cluster 0xFC32 (64562), endpoint 1, server
  command 0x00 ``mmwave_control_command`` with ``control_id`` 1 (generate)
  or 3 (clear).
- VZW32-SN (Z-Wave JS): configuration parameter 111 "mmWave Control
  Command", value 1 (generate) or 3 (clear).

Every other value of those commands (factory reset, reset detection area,
...) stays blocked: the call is re-checked against
``actions.mmwave_call_allowed`` before it is sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr

from .actions import ActionRefused, mmwave_call_allowed
from .bindings import zha_ieee

MANUFACTURER = "Inovelli"
MODEL_ZHA = "VZM32-SN"
MODEL_ZWAVE = "VZW32-SN"
MODELS = {MODEL_ZHA: "zha", MODEL_ZWAVE: "zwave_js"}

COMMANDS = {"generate": 1, "clear": 3}

MMWAVE_CLUSTER = 0xFC32  # 64562, InovelliVZM32SNMMWaveCluster
MMWAVE_ENDPOINT = 1
MMWAVE_CONTROL_COMMAND = 0x00  # mmwave_control_command(control_id: enum8)
ZWAVE_PARAMETER = 111  # mmWave Control Command

TEXT_GENERATE = (
    "The switch scans the room for about 20 seconds; its LED bar pulses orange "
    "while it runs. Anything moving in the detection area during the scan — "
    "including a person — is stored as interference and ignored from then on, "
    "so leave the area or keep still and out of view. "
    "Undo: Clear interference area."
)
TEXT_CLEAR = (
    "Removes the stored interference area: everything moving in the detection "
    "area counts as motion again. To store a new one, use Generate interference "
    "area."
)
SENT_TEXT = "Sent — watch for the orange LED (~20 s)"


@dataclass(frozen=True)
class MMWaveCall:
    """One Home Assistant action call."""

    domain: str
    service: str
    data: dict[str, Any]
    target: dict[str, Any] | None


def device_model(device: dr.DeviceEntry) -> str | None:
    """VZM32-SN or VZW32-SN for an Inovelli mmWave switch, else None."""
    if (device.manufacturer or "") != MANUFACTURER:
        return None
    return device.model if device.model in MODELS else None


def is_mmwave_device(device: dr.DeviceEntry) -> bool:
    """True for a device the component shows on."""
    model = device_model(device)
    if model is None:
        return False
    return any(domain == MODELS[model] for domain, _ in device.identifiers)


def build_call(device: dr.DeviceEntry, command: str) -> MMWaveCall:
    """The action for this device and command; raises ActionRefused."""
    if command not in COMMANDS:
        raise ActionRefused("Unknown mmWave command")
    value = COMMANDS[command]
    model = device_model(device)
    if model == MODEL_ZHA:
        ieee = zha_ieee(device)
        if ieee is None:
            raise ActionRefused("This device has no Zigbee IEEE address")
        call = MMWaveCall(
            "zha",
            "issue_zigbee_cluster_command",
            {
                "ieee": ieee,
                "endpoint_id": MMWAVE_ENDPOINT,
                "cluster_id": MMWAVE_CLUSTER,
                "cluster_type": "in",
                "command": MMWAVE_CONTROL_COMMAND,
                "command_type": "server",
                "params": {"control_id": value},
            },
            None,
        )
    elif model == MODEL_ZWAVE:
        if not any(domain == "zwave_js" for domain, _ in device.identifiers):
            raise ActionRefused("This device is not a Z-Wave JS device")
        call = MMWaveCall(
            "zwave_js",
            "set_config_parameter",
            {"parameter": ZWAVE_PARAMETER, "value": value},
            {"device_id": device.id},
        )
    else:
        raise ActionRefused("Only Inovelli VZM32-SN and VZW32-SN switches support this")
    if not mmwave_call_allowed(call.domain, call.service, call.data, call.target, model, device):
        raise ActionRefused("This mmWave command is blocked by Custom Device Page Components")
    return call


async def async_send(
    hass: HomeAssistant, device_id: str, command: str, user_id: str
) -> None:
    """Send Generate / Clear to the switch as the person who pressed it."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ActionRefused("Device not found")
    call = build_call(device, command)
    if not hass.services.has_service(call.domain, call.service):
        raise ActionRefused(f"{call.domain}.{call.service} is not available")
    await hass.services.async_call(
        call.domain,
        call.service,
        dict(call.data),
        blocking=True,
        target=call.target,
        context=Context(user_id=user_id),
    )


def component_payload(order: int, column: int) -> dict[str, Any]:
    """The card the device page shows."""
    return {
        "id": "mmwave",
        "type": "mmwave",
        "title": "mmWave interference area",
        "order": order,
        "column": column,
        "description": (
            "Store what is moving in the room right now (HVAC airflow, fans, "
            "curtains) as interference, so it no longer counts as motion."
        ),
        "buttons": [
            {"command": "generate", "label": "Generate interference area", "confirm_text": TEXT_GENERATE},
            {"command": "clear", "label": "Clear interference area", "confirm_text": TEXT_CLEAR},
        ],
        "sent_text": SENT_TEXT,
    }
