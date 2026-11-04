"""Small compatibility helpers across Home Assistant releases."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr


def device_entry_ids(device: dr.DeviceEntry) -> set[str]:
    """Config entries a device belongs to.

    Newer releases give every device exactly one config entry
    (``config_entry_id``) and report reads of the old ``config_entries`` set
    as deprecated; older releases only have the set.
    """
    if (entry_id := getattr(device, "config_entry_id", None)) is not None:
        return {entry_id}
    if hasattr(type(device), "config_entry_id"):
        return set()
    return set(device.config_entries)


def device_primary_entry(device: dr.DeviceEntry) -> str | None:
    """The device's main config entry."""
    if hasattr(type(device), "config_entry_id") or hasattr(device, "config_entry_id"):
        return getattr(device, "config_entry_id", None)
    return device.primary_config_entry


def all_devices(dev_reg: dr.DeviceRegistry) -> list[dr.DeviceEntry]:
    """Every device entry in the registry.

    Since 2026.10 iterating ``DeviceRegistry.devices`` yields the entries and any
    mapping access (``.values()``, ``.keys()``, ``[id]``, ``id in``) is reported
    as deprecated (removed in 2027.9). Older releases return a plain mapping,
    whose iteration yields device ids.
    """
    items = list(dev_reg.devices)
    if items and isinstance(items[0], str):
        return [dev_reg.async_get(device_id) for device_id in items]
    return items
