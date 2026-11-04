"""Shared entity helpers."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .bindings import _app, zha_ieee
from .const import DOMAIN, NAME, VERSION


def hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    """The integration's own service device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
        manufacturer="Custom Device Page Components",
        model="Device page components",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
    )


@callback
def zha_devices(hass: HomeAssistant) -> list[tuple[dr.DeviceEntry, str]]:
    """(device, ieee) for ZHA devices except the coordinator."""
    dev_reg = dr.async_get(hass)
    found = [(d, ieee) for d in dev_reg.devices.values() if (ieee := zha_ieee(d))]
    coordinator = None
    if app := _app(hass):
        try:
            coordinator = str(app.state.node_info.ieee)
        except AttributeError:
            coordinator = None
    if coordinator is None:
        # Without ZHA running, the coordinator is the device others hang off.
        parents = {d.via_device_id for d, _ in found if d.via_device_id}
        return [(d, i) for d, i in found if d.id not in parents]
    return [(d, i) for d, i in found if i != coordinator]
