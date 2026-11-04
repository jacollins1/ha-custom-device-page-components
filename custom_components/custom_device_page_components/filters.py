"""Which device pages a component appears on.

Filter groups are combined with AND; values within one group with OR
(status has two groups: enabled/disabled and available/unavailable). An
empty group does not restrict. With every group empty the component shows
on all device pages.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .compat import device_entry_ids

# Device "type": the entry type, or manufacturer + model.
TYPE_SERVICE = "entry_type:service"
TYPE_DEVICE = "entry_type:device"

STATUS_VALUES = ["enabled", "disabled", "available", "unavailable"]


def model_key(manufacturer: str | None, model: str | None) -> str:
    """Stable key for a manufacturer/model pair."""
    return f"model:{manufacturer or ''}|{model or ''}"


def type_options(hass: HomeAssistant) -> list[dict[str, str]]:
    """Device types present in this installation, for the builder."""
    opts = {
        TYPE_DEVICE: "Physical devices (not services)",
        TYPE_SERVICE: "Services",
    }
    for device in dr.async_get(hass).devices.values():
        if device.manufacturer or device.model:
            key = model_key(device.manufacturer, device.model)
            opts[key] = " ".join(x for x in (device.manufacturer, device.model) if x)
    head = [{"value": k, "label": v} for k, v in list(opts.items())[:2]]
    rest = sorted(
        ({"value": k, "label": v} for k, v in list(opts.items())[2:]),
        key=lambda o: o["label"].lower(),
    )
    return head + rest


def integration_options(hass: HomeAssistant) -> list[dict[str, str]]:
    """Integrations that own at least one device."""
    domains: set[str] = set()
    for device in dr.async_get(hass).devices.values():
        for entry_id in device_entry_ids(device):
            if entry := hass.config_entries.async_get_entry(entry_id):
                domains.add(entry.domain)
    return [{"value": d, "label": d} for d in sorted(domains)]


def device_matches(
    hass: HomeAssistant,
    device: dr.DeviceEntry,
    flt: dict[str, Any] | None,
    available: bool,
) -> bool:
    """Return True if the component should show on this device's page."""
    flt = flt or {}
    types = flt.get("types") or []
    if types:
        own = {
            TYPE_SERVICE
            if device.entry_type is dr.DeviceEntryType.SERVICE
            else TYPE_DEVICE,
            model_key(device.manufacturer, device.model),
        }
        if not own & set(types):
            return False
    statuses = set(flt.get("statuses") or [])
    # Status has two independent dimensions; each is checked on its own.
    enabled_vals = statuses & {"enabled", "disabled"}
    if enabled_vals and ("disabled" if device.disabled_by else "enabled") not in enabled_vals:
        return False
    avail_vals = statuses & {"available", "unavailable"}
    if avail_vals and ("available" if available else "unavailable") not in avail_vals:
        return False
    integrations = flt.get("integrations") or []
    if integrations:
        domains = {
            entry.domain
            for entry_id in device_entry_ids(device)
            if (entry := hass.config_entries.async_get_entry(entry_id))
        }
        if not domains & set(integrations):
            return False
    areas = flt.get("areas") or []
    if areas and device.area_id not in areas:
        return False
    return True
