"""Buttons: check & reload the card, read Zigbee bindings."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .bindings import BindingsManager
from .entity import hub_device_info, zha_devices


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    runtime = entry.runtime_data
    hub: list[ButtonEntity] = [CheckReloadButton(entry)]
    if runtime.bindings is not None:
        hub.append(ReadAllButton(entry, runtime.bindings))
    async_add_entities(hub)
    if runtime.bindings is None:
        return
    created: set[str] = set()

    @callback
    def _add() -> None:
        new = []
        for device, ieee in zha_devices(hass):
            if device.id not in created:
                created.add(device.id)
                new.append(ReadBindingsButton(runtime.bindings, device, ieee))
        if new:
            async_add_entities(new)

    _add()

    @callback
    def _device_added(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        if event.data["action"] == "create":
            _add()

    entry.async_on_unload(
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _device_added)
    )


class CheckReloadButton(ButtonEntity):
    """Check the module file and register it again with a fresh URL."""

    _attr_has_entity_name = True
    _attr_translation_key = "check_reload"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_checkreload"
        self._attr_device_info = hub_device_info(entry)

    async def async_press(self) -> None:
        from . import async_mark_reloaded, async_register_module  # noqa: PLC0415

        runtime = self._entry.runtime_data
        if await async_register_module(self.hass, runtime):
            async_mark_reloaded(self.hass, runtime)


class ReadAllButton(ButtonEntity):
    """Read every mains-powered Zigbee device's bindings, one at a time."""

    _attr_has_entity_name = True
    _attr_translation_key = "read_all_bindings"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, mgr: BindingsManager) -> None:
        self._mgr = mgr
        self._attr_unique_id = f"{entry.entry_id}_readall"
        self._attr_device_info = hub_device_info(entry)

    async def async_press(self) -> None:
        self._mgr.async_read_all()


class ReadBindingsButton(ButtonEntity):
    """Read this device's binding table (queues sleepy devices)."""

    _attr_has_entity_name = True
    _attr_translation_key = "read_bindings"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, mgr: BindingsManager, device: dr.DeviceEntry, ieee: str) -> None:
        self._mgr = mgr
        self._ieee = ieee
        self._attr_unique_id = f"{ieee}_readbindings"
        self.device_entry = device

    async def async_press(self) -> None:
        await self._mgr.async_request_read(self._ieee)
