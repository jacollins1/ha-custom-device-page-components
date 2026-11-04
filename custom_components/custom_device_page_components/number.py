"""Built-in component: Color temperature boxes on light device pages.

Up to two number entities per color-temperature light that belongs to a
device: one in mireds ("Color temperature") and one in kelvin ("Color
temperature (kelvin)"), each switched on or off in the integration options. Each
is linked to the light's device (Entity.device_entry), so it shows under
Configuration on that device's page without adding this integration to the
device. It reads the light's state and writes with light.turn_on, so it
makes no device traffic of its own.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)

from .const import (
    CONF_COLOR_TEMP_KELVIN,
    CONF_COLOR_TEMP_MIREDS,
    CONF_COLOR_TEMP_MODELS,
    KIND_CT_KELVIN,
    KIND_CT_MIREDS,
)


def eligible_lights(hass: HomeAssistant, models: list[str]) -> list[er.RegistryEntry]:
    """Light registry entries that should get a box."""
    dev_reg = dr.async_get(hass)
    wanted = {m.lower() for m in models}
    out = []
    for entry in er.async_get(hass).entities.values():
        if entry.domain != "light" or not entry.device_id or entry.disabled_by:
            continue
        modes = (entry.capabilities or {}).get("supported_color_modes") or []
        if "color_temp" not in modes:
            continue
        device = dev_reg.async_get(entry.device_id)
        if device is None:
            continue
        if wanted and not {
            (device.model or "").lower(), (device.model_id or "").lower()
        } & wanted:
            continue
        out.append(entry)
    return out


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create boxes now and for lights added later."""
    units = [
        mireds
        for mireds, key in ((True, CONF_COLOR_TEMP_MIREDS), (False, CONF_COLOR_TEMP_KELVIN))
        if entry.options.get(key, True)
    ]
    if not units:
        return
    models = entry.options.get(CONF_COLOR_TEMP_MODELS, [])
    created: set[str] = set()

    @callback
    def _add(lights: list[er.RegistryEntry]) -> None:
        new = [
            ColorTempNumber(hass, light, mireds)
            for light in lights
            if light.id not in created
            for mireds in units
        ]
        created.update(e.source_id for e in new)
        if new:
            async_add_entities(new)

    _add(eligible_lights(hass, models))

    @callback
    def _registry_changed(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] in ("create", "update") and event.data[
            "entity_id"
        ].startswith("light."):
            _add(eligible_lights(hass, models))

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_changed)
    )


class ColorTempNumber(NumberEntity):
    """Current color temperature of a light, editable."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_should_poll = False
    _attr_translation_key = "color_temperature"

    def __init__(self, hass: HomeAssistant, light: er.RegistryEntry, mireds: bool) -> None:
        self.hass = hass
        self.source_id = light.id
        self._mireds = mireds
        self._attr_unique_id = f"{light.id}_{KIND_CT_MIREDS if mireds else KIND_CT_KELVIN}"
        self._attr_translation_key = "color_temperature" if mireds else "color_temperature_kelvin"
        self.device_entry = dr.async_get(hass).async_get(light.device_id)
        caps = light.capabilities or {}
        kmin = caps.get("min_color_temp_kelvin") or 2000
        kmax = caps.get("max_color_temp_kelvin") or 6500
        if mireds:
            self._attr_native_unit_of_measurement = "mired"
            self._attr_native_min_value = math.floor(1_000_000 / kmax)
            self._attr_native_max_value = math.floor(1_000_000 / kmin)
        else:
            self._attr_native_unit_of_measurement = UnitOfTemperature.KELVIN
            self._attr_native_min_value = kmin
            self._attr_native_max_value = kmax
        # A device with several color-temperature lights gets one box each.
        siblings = [
            e for e in er.async_entries_for_device(er.async_get(hass), light.device_id)
            if e.domain == "light" and "color_temp" in
            ((e.capabilities or {}).get("supported_color_modes") or [])
        ]
        if len(siblings) > 1:
            self._attr_translation_placeholders = {
                "light": light.name or light.original_name or light.entity_id
            }
            self._attr_translation_key = f"{self._attr_translation_key}_of"
        self._unsub_state = None
        self._unsub_reg = None

    @property
    def source_entity_id(self) -> str | None:
        """Current entity id of the light (follows renames)."""
        entry = er.async_get(self.hass).async_get(self.source_id)
        return entry.entity_id if entry else None

    async def async_added_to_hass(self) -> None:
        """Follow the light's state and registry entry."""
        self._subscribe()
        self._follow_device()

    async def async_will_remove_from_hass(self) -> None:
        """Stop following."""
        for unsub in (self._unsub_state, self._unsub_reg):
            if unsub:
                unsub()
        self._unsub_state = self._unsub_reg = None

    @callback
    def _subscribe(self) -> None:
        """(Re)subscribe under the light's current entity id."""
        for unsub in (self._unsub_state, self._unsub_reg):
            if unsub:
                unsub()
        self._unsub_state = self._unsub_reg = None
        if eid := self.source_entity_id:
            self._unsub_state = async_track_state_change_event(
                self.hass, eid, self._state_event
            )
            self._unsub_reg = async_track_entity_registry_updated_event(
                self.hass, eid, self._source_registry_event
            )

    @callback
    def _state_event(self, _: Event) -> None:
        self.async_write_ha_state()

    @callback
    def _source_registry_event(self, event: Event) -> None:
        if event.data["action"] == "remove":
            # The light is gone: remove this box too.
            self.hass.async_create_task(self._remove_self())
            return
        self._subscribe()
        self._follow_device()
        self.async_write_ha_state()

    @callback
    def _follow_device(self) -> None:
        """Stay on the light's device when the light moves to another one.

        Home Assistant 2026.11 moves each ZHA group light from the coordinator
        to a device of its own, for example.
        """
        light = er.async_get(self.hass).async_get(self.source_id)
        mine = self.registry_entry
        if light is None or mine is None or not light.device_id:
            return
        if light.device_id != mine.device_id:
            self.device_entry = dr.async_get(self.hass).async_get(light.device_id)
            er.async_get(self.hass).async_update_entity(
                self.entity_id, device_id=light.device_id
            )

    async def _remove_self(self) -> None:
        ent_reg = er.async_get(self.hass)
        if self.registry_entry:
            ent_reg.async_remove(self.entity_id)
        else:
            await self.async_remove()

    @property
    def available(self) -> bool:
        """Unavailable when the light is."""
        eid = self.source_entity_id
        state = self.hass.states.get(eid) if eid else None
        return state is not None and state.state != STATE_UNAVAILABLE

    @property
    def native_value(self) -> float | None:
        """Empty while the light is off or showing a color."""
        eid = self.source_entity_id
        state = self.hass.states.get(eid) if eid else None
        if state is None or state.state != "on":
            return None
        if state.attributes.get("color_mode") != "color_temp":
            return None
        kelvin = state.attributes.get(ATTR_COLOR_TEMP_KELVIN)
        if not kelvin:
            return None
        return math.floor(1_000_000 / kelvin) if self._mireds else kelvin

    async def async_set_native_value(self, value: float) -> None:
        """Set the temperature (turns the light on)."""
        eid = self.source_entity_id
        if eid is None:
            return
        if self._mireds:
            # floor(1e6 / floor(1e6 / m)) == m for the mired range of bulbs,
            # so the box reads back exactly what was typed.
            kelvin = math.floor(1_000_000 / value)
        else:
            kelvin = int(value)
        data: dict[str, Any] = {ATTR_COLOR_TEMP_KELVIN: kelvin}
        await self.hass.services.async_call(
            "light", "turn_on", data, target={"entity_id": eid}, blocking=True,
            context=self._context,
        )
