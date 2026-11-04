"""Row actions: safety policy and execution.

Row actions can only call Home Assistant actions (services), always with the
context of the person who pressed the button, so Home Assistant's own
permission checks apply. The integration never edits another integration's
configuration, registries, files or databases.

Policy levels:
- blocked: never offered in the builder and refused at run time.
- confirm: offered, but the device page asks for confirmation every time.
- allowed: runs on a single press (a confirmation can still be turned on).
"""

from __future__ import annotations

from typing import Any, Literal

from homeassistant.core import Context, HomeAssistant

Level = Literal["blocked", "confirm", "allowed"]

# Whole domains that stop, restart, uninstall, restore, purge or run code.
BLOCKED_DOMAINS = {
    "hassio",  # app stop/uninstall, host reboot/shutdown, backup restore
    "backup",
    "recorder",  # purge, disable, import statistics
    "shell_command",
    "python_script",
    "pyscript",
    "command_line",
    "homeassistant",  # stop, restart, set_location, reload_all, ...
    "system_log",
    "logger",
    "frontend",  # set_theme for everyone
    "marketplace",
    "hacs",
}

# Any action whose name contains one of these words is blocked.
BLOCKED_WORDS = (
    "remove", "delete", "purge", "reset", "factory", "clear", "restore",
    "uninstall", "reboot", "shutdown", "wipe", "format", "unpair", "leave",
    "permit", "exclude", "include", "replace", "rebuild", "heal",
    "set_zigbee_cluster_attribute", "issue_zigbee", "set_config_parameter",
    "bulk_set", "invoke_cc_api", "refresh_value", "migrate",
)

# Explicit blocks across integrations (device-network management).
BLOCKED_ACTIONS = {
    "zha.permit", "zha.remove", "zha.set_lock_user_code",
    "zha.clear_lock_user_code", "zwave_js.set_config_parameter",
    "zwave_js.bulk_set_partial_config_parameters", "zwave_js.reset_meter",
    "update.install", "update.skip", "update.clear_skipped",
    "lock.set_usercode", "lock.clear_usercode",
}

# Safety- or security-relevant actions that always need a confirmation.
CONFIRM_ACTIONS = {
    "lock.unlock", "lock.open",
    "alarm_control_panel.alarm_disarm",
    "alarm_control_panel.alarm_arm_away",
    "alarm_control_panel.alarm_arm_home",
    "alarm_control_panel.alarm_arm_night",
    "alarm_control_panel.alarm_arm_vacation",
    "alarm_control_panel.alarm_trigger",
    "cover.open_cover", "cover.close_cover", "cover.toggle",
    "valve.open_valve", "valve.close_valve", "valve.toggle",
    "climate.set_hvac_mode", "climate.turn_off",
    "water_heater.turn_off", "water_heater.set_operation_mode",
    "vacuum.start", "lawn_mower.start_mowing",
    "button.press",
    "switch.turn_off", "switch.toggle",
    "automation.turn_off", "automation.toggle",
    "script.turn_on", "script.toggle",
    "input_boolean.turn_off", "input_boolean.toggle",
    "notify.send_message", "tts.speak",
}

# Button device classes that are never pressed from a row action.
BLOCKED_BUTTON_CLASSES = {"restart", "update"}


def action_level(action: str) -> Level:
    """Classify a domain.service string."""
    action = action.strip().lower()
    if "." not in action:
        return "blocked"
    domain, _, service = action.partition(".")
    if domain in BLOCKED_DOMAINS or action in BLOCKED_ACTIONS:
        return "blocked"
    if any(word in service for word in BLOCKED_WORDS):
        return "blocked"
    if action in CONFIRM_ACTIONS:
        return "confirm"
    return "allowed"


def available_actions(hass: HomeAssistant) -> list[dict[str, str]]:
    """Every registered action that is not blocked, for the builder."""
    out: list[dict[str, str]] = []
    for domain, services in hass.services.async_services().items():
        for service in services:
            name = f"{domain}.{service}"
            level = action_level(name)
            if level == "blocked":
                continue
            out.append({"value": name, "label": f"{name} ({level})"})
    out.sort(key=lambda x: x["value"])
    return out


class ActionRefused(Exception):
    """The action is not allowed; message is shown to the user."""


async def async_run_action(
    hass: HomeAssistant,
    action_cfg: dict[str, Any],
    target_value: Any,
    user_id: str,
    confirmed: bool,
) -> None:
    """Run one row action after re-checking the policy."""
    action = str(action_cfg.get("action") or "")
    level = action_level(action)
    if level == "blocked":
        raise ActionRefused(f"{action} is blocked by Custom Device Page Components")
    if (level == "confirm" or action_cfg.get("confirm")) and not confirmed:
        raise ActionRefused("This action needs a confirmation")
    domain, _, service = action.partition(".")
    if not hass.services.has_service(domain, service):
        raise ActionRefused(f"{action} is not available")

    data: dict[str, Any] = dict(action_cfg.get("data") or {})
    target_kind = action_cfg.get("target_kind") or "entity_id"
    if target_value in (None, ""):
        raise ActionRefused("This row has no target")
    if target_kind not in ("entity_id", "device_id", "area_id"):
        raise ActionRefused("Unknown target type")
    if target_kind == "entity_id" and domain == "button":
        state = hass.states.get(str(target_value))
        if state and state.attributes.get("device_class") in BLOCKED_BUTTON_CLASSES:
            raise ActionRefused("Restart and update buttons can't be pressed here")
    # Targets come from the row the server just re-queried, never from the
    # browser, so a row can't be made to point somewhere else.
    await hass.services.async_call(
        domain,
        service,
        data,
        blocking=True,
        target={target_kind: target_value},
        context=Context(user_id=user_id),
    )
