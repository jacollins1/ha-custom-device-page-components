"""Sensors: device-page card status, Zigbee bound to / bound from."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .bindings import ST_OK, BindingsManager
from .const import (
    MIN_HA_VERSION,
    SIGNAL_ATTACH_REPORT,
    SIGNAL_BINDINGS_UPDATED,
    TESTED_HA_VERSION,
    VERSION,
)
from .entity import hub_device_info, zha_devices


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    runtime = entry.runtime_data
    async_add_entities([CardStatusSensor(entry)])
    if runtime.bindings is None:
        return
    created: set[str] = set()

    @callback
    def _add() -> None:
        new: list[SensorEntity] = []
        for device, ieee in zha_devices(hass):
            if device.id in created:
                continue
            created.add(device.id)
            new += [
                BoundToSensor(runtime.bindings, device, ieee),
                BoundFromSensor(runtime.bindings, device, ieee),
            ]
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


class CardStatusSensor(SensorEntity):
    """Whether browsers could attach the card to device pages."""

    _attr_has_entity_name = True
    _attr_translation_key = "card_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"history"})

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_cardstatus"
        self._attr_device_info = hub_device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ATTACH_REPORT, self.async_write_ha_state)
        )

    @property
    def native_value(self) -> str:
        return self._entry.runtime_data.attach.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        a = self._entry.runtime_data.attach
        return {
            "reason": a.reason,
            "reported_ha_version": a.ha_version,
            "reported_module": a.module_version,
            "reported_at": a.reported_at,
            "module_url": self._entry.runtime_data.module_url,
            "integration_version": VERSION,
            "minimum_ha_version": MIN_HA_VERSION,
            "tested_ha_version": TESTED_HA_VERSION,
            "history": a.history,
        }


class _BindingSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"bindings", "coordinator_reporting"})

    def __init__(self, mgr: BindingsManager, device: dr.DeviceEntry, ieee: str) -> None:
        self._mgr = mgr
        self._ieee = ieee
        self.device_entry = device

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated(ieee: str | None) -> None:
            if ieee is None or ieee == self._ieee:
                self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_BINDINGS_UPDATED, _updated)
        )


def _summary(items: list[str], empty: str) -> str:
    text = "; ".join(items) if items else empty
    return text if len(text) <= 255 else text[:252] + "…"


class BoundToSensor(_BindingSensor):
    """What this device's binding table points at."""

    _attr_translation_key = "bound_to"

    def __init__(self, mgr: BindingsManager, device: dr.DeviceEntry, ieee: str) -> None:
        super().__init__(mgr, device, ieee)
        self._attr_unique_id = f"{ieee}_boundto"

    @property
    def native_value(self) -> str:
        status = self._mgr.status_for(self._ieee)
        if status != ST_OK:
            return status
        return _summary(self._mgr.bound_to(self._ieee)["items"], "No bindings")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._mgr.bound_to(self._ieee)
        return {
            "bindings": data["items"],
            "coordinator_reporting": data["coordinator_reporting"],
            "read_at": data["read_at"],
        }


class BoundFromSensor(_BindingSensor):
    """Other devices bound to this one (from tables that have been read)."""

    _attr_translation_key = "bound_from"

    def __init__(self, mgr: BindingsManager, device: dr.DeviceEntry, ieee: str) -> None:
        super().__init__(mgr, device, ieee)
        self._attr_unique_id = f"{ieee}_boundfrom"

    @property
    def native_value(self) -> str:
        data = self._mgr.bound_from(self._ieee)
        if not data["tables_read"]:
            return "No tables read yet"
        return _summary(data["items"], "None found")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._mgr.bound_from(self._ieee)
        return {"bindings": data["items"], "tables_read": data["tables_read"]}
