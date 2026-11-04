"""Read-only snapshot of Home Assistant data for queries and Related items.

Everything here only reads public registries, states and dashboard configs.
Nothing is written to another integration's data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
import logging
import re
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
    label_registry as lr,
)
from homeassistant.loader import async_get_loaded_integration

from .compat import device_entry_ids, device_primary_entry
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ENTITY_ID_RE = re.compile(r"\b([a-z0-9_]+\.[a-z0-9_]+)\b")
DEVICE_ID_RE = re.compile(r"\b([0-9a-f]{32})\b")


@dataclass
class DashboardInfo:
    """A dashboard and the device/entity ids each of its views mentions."""

    url_path: str  # path used in links ("lovelace" for the default)
    title: str
    mode: str
    require_admin: bool
    show_in_sidebar: bool
    strategy: bool
    # (view_index, view_path, view_title, set of tokens)
    views: list[tuple[int, str | None, str | None, set[str]]] = field(
        default_factory=list
    )


def _integration_info(hass: HomeAssistant, domain: str) -> tuple[str, str]:
    try:
        integ = async_get_loaded_integration(hass, domain)
    except Exception:  # noqa: BLE001 - integration not loaded
        return domain, "unknown"
    return integ.name, integ.integration_type or "hub"


def _tokens(obj: Any) -> set[str]:
    text = json.dumps(obj, default=str)
    return set(ENTITY_ID_RE.findall(text)) | set(DEVICE_ID_RE.findall(text))


class SnapshotManager:
    """Builds and caches the data the queries and Related card read."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._dashboards: list[DashboardInfo] | None = None
        self._dashboards_at = 0.0
        self._unsubs: list = []

    @callback
    def async_start(self) -> None:
        """Invalidate the dashboard cache when a dashboard changes."""

        @callback
        def _invalidate(_: Event) -> None:
            self._dashboards = None

        self._unsubs.append(
            self.hass.bus.async_listen("lovelace_updated", _invalidate)
        )

    @callback
    def async_stop(self) -> None:
        """Stop listening."""
        while self._unsubs:
            self._unsubs.pop()()

    # ------------------------------------------------------------------
    # Dashboards (read through Lovelace's own loaders)
    # ------------------------------------------------------------------

    async def async_dashboards(self) -> list[DashboardInfo]:
        """Return dashboard info, loading configs on first use."""
        # Dashboard edits fire lovelace_updated; creating or deleting a
        # dashboard does not, so the cache also expires after a minute.
        if self._dashboards is not None and time.monotonic() - self._dashboards_at < 60:
            return self._dashboards
        result: list[DashboardInfo] = []
        try:
            from homeassistant.components.lovelace.const import (  # noqa: PLC0415
                LOVELACE_DATA,
            )

            data = self.hass.data.get(LOVELACE_DATA)
        except ImportError:
            data = None
        if data is None:
            self._dashboards = result
            return result
        for url_path, dash in list(data.dashboards.items()):
            meta = getattr(dash, "config", None) or {}
            try:
                config = await dash.async_load(False)
            except Exception:  # noqa: BLE001 - empty/unconfigured dashboard
                continue
            if not isinstance(config, dict):
                continue
            info = DashboardInfo(
                url_path=url_path or "lovelace",
                title=meta.get("title") or ("Overview" if not url_path else url_path),
                mode=getattr(dash, "mode", "storage"),
                require_admin=bool(meta.get("require_admin", False)),
                show_in_sidebar=bool(meta.get("show_in_sidebar", True)),
                strategy="strategy" in config,
            )
            for idx, view in enumerate(config.get("views") or []):
                if not isinstance(view, dict):
                    continue
                info.views.append(
                    (idx, view.get("path"), view.get("title"), _tokens(view))
                )
            result.append(info)
        self._dashboards = result
        self._dashboards_at = time.monotonic()
        return result

    # ------------------------------------------------------------------
    # Device context
    # ------------------------------------------------------------------

    @callback
    def device_context(self, device_id: str) -> dict[str, Any] | None:
        """Fields of a device usable as WHERE values."""
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return None
        primary = device_primary_entry(device)
        entry = self.hass.config_entries.async_get_entry(primary) if primary else None
        return {
            "id": device.id,
            "display_name": device.name_by_user or device.name,
            "area_id": device.area_id,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "primary_config_entry": primary,
            "integration": entry.domain if entry else None,
            "via_device_id": device.via_device_id,
        }

    @callback
    def device_available(self, device_id: str) -> bool:
        """True if any enabled entity of the device is not unavailable."""
        ent_reg = er.async_get(self.hass)
        entries = er.async_entries_for_device(ent_reg, device_id)
        if not entries:
            return True
        for entry in entries:
            state = self.hass.states.get(entry.entity_id)
            if state is not None and state.state != STATE_UNAVAILABLE:
                return True
        return False

    # ------------------------------------------------------------------
    # Related items
    # ------------------------------------------------------------------

    async def async_related(self, device_id: str) -> dict[str, list[dict]]:
        """Helpers, integration entries and dashboards related to a device."""
        index = await self.async_relations_index()
        out: dict[str, list[dict]] = {"helpers": [], "entries": [], "dashboards": []}
        for rel in index.get(device_id, []):
            out[rel["category"]].append(rel)
        for items in out.values():
            items.sort(key=lambda r: (r["title"] or "").lower())
        return out

    async def async_relations_index(self) -> dict[str, list[dict]]:
        """device_id -> list of relation dicts, for every device."""
        hass = self.hass
        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)
        entity_to_device: dict[str, str] = {
            e.entity_id: e.device_id for e in ent_reg.entities.values() if e.device_id
        }
        device_ids = set(dev_reg.devices.keys())

        def devices_for(tokens: set[str]) -> set[str]:
            found = {t for t in tokens if t in device_ids}
            found |= {entity_to_device[t] for t in tokens if t in entity_to_device}
            return found

        index: dict[str, list[dict]] = {}
        seen: set[tuple[str, str, str]] = set()

        def add(device_id: str, rel: dict) -> None:
            key = (device_id, rel["category"], rel["link_target"])
            if key in seen:
                return
            seen.add(key)
            index.setdefault(device_id, []).append({"device_id": device_id, **rel})

        # 1. The device's own integration entries.
        entries = {e.entry_id: e for e in hass.config_entries.async_entries()}
        for device in dev_reg.devices.values():
            for entry_id in device_entry_ids(device):
                entry = entries.get(entry_id)
                if entry is None or entry.domain == DOMAIN:
                    continue
                name, itype = _integration_info(hass, entry.domain)
                add(device.id, _entry_rel(entry, name, itype, "Device integration"))

        # 2. Entries whose stored settings name the device or its entities.
        for entry in entries.values():
            if entry.domain == DOMAIN:
                continue
            payload = {
                "data": dict(entry.data),
                "options": dict(entry.options),
                "subentries": [dict(s.data) for s in entry.subentries.values()],
            }
            hits = devices_for(_tokens(payload))
            if not hits:
                continue
            name, itype = _integration_info(hass, entry.domain)
            for dev_id in hits:
                if entry.entry_id in device_entry_ids(dev_reg.devices[dev_id]):
                    continue
                add(dev_id, _entry_rel(entry, name, itype, "Uses this device"))

        # 3. Group-type entities without a config entry (YAML groups etc.)
        #    whose members are this device's entities.
        for state in hass.states.async_all():
            members = state.attributes.get("entity_id")
            if not isinstance(members, (list, tuple)):
                continue
            reg = ent_reg.async_get(state.entity_id)
            if reg is not None and reg.config_entry_id:
                continue  # covered by step 2
            for dev_id in devices_for(set(members)):
                if entity_to_device.get(state.entity_id) == dev_id:
                    continue
                add(
                    dev_id,
                    {
                        "category": "helpers",
                        "title": state.name,
                        "subtitle": f"Group ({state.domain}, YAML)",
                        "link_type": "entity",
                        "link_target": state.entity_id,
                    },
                )

        # 4. Dashboards.
        for dash in await self.async_dashboards():
            for idx, path, vtitle, tokens in dash.views:
                for dev_id in devices_for(tokens):
                    view = path or str(idx)
                    add(
                        dev_id,
                        {
                            "category": "dashboards",
                            "title": f"{dash.title} › {vtitle or view}",
                            "subtitle": "Admin only" if dash.require_admin else "",
                            "link_type": "url",
                            "link_target": f"/{dash.url_path}/{view}",
                        },
                    )
        return index

    # ------------------------------------------------------------------
    # Query tables
    # ------------------------------------------------------------------

    async def async_tables(self, needed: set[str]) -> dict[str, list[tuple]]:
        """Rows for the tables a query uses (built in the event loop)."""
        hass = self.hass
        out: dict[str, list[tuple]] = {}
        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)
        entries = {e.entry_id: e for e in hass.config_entries.async_entries()}

        def domain_of(entry_id: str | None) -> str | None:
            entry = entries.get(entry_id) if entry_id else None
            return entry.domain if entry else None

        if "devices" in needed:
            out["devices"] = [
                (
                    d.id, d.name_by_user or d.name, d.name, d.name_by_user,
                    d.manufacturer, d.model, d.model_id, d.sw_version,
                    d.hw_version, d.serial_number, d.area_id,
                    d.entry_type.value if d.entry_type else "device",
                    d.disabled_by.value if d.disabled_by else None,
                    d.via_device_id, device_primary_entry(d),
                    domain_of(device_primary_entry(d)), d.configuration_url,
                    ",".join(sorted(d.labels)),
                    1 if self.device_available(d.id) else 0,
                )
                for d in dev_reg.devices.values()
            ]
        if "device_config_entries" in needed:
            out["device_config_entries"] = [
                (d.id, eid, domain_of(eid))
                for d in dev_reg.devices.values()
                for eid in device_entry_ids(d)
            ]
        if "entities" in needed:
            rows = []
            for e in ent_reg.entities.values():
                eff_area = e.area_id
                if eff_area is None and e.device_id:
                    dev = dev_reg.async_get(e.device_id)
                    eff_area = dev.area_id if dev else None
                state = hass.states.get(e.entity_id)
                rows.append(
                    (
                        e.entity_id, e.id, e.unique_id, e.platform, e.domain,
                        e.device_id, e.area_id, eff_area, e.config_entry_id,
                        state.name if state else (e.name or e.original_name),
                        e.name, e.original_name,
                        e.disabled_by.value if e.disabled_by else None,
                        e.hidden_by.value if e.hidden_by else None,
                        e.entity_category.value if e.entity_category else None,
                        e.device_class or e.original_device_class,
                        e.unit_of_measurement, e.icon or e.original_icon,
                        ",".join(sorted(e.labels)),
                    )
                )
            out["entities"] = rows
        if "states" in needed:
            out["states"] = [
                (
                    s.entity_id, s.state, s.last_changed.isoformat(),
                    s.last_updated.isoformat(), s.name,
                    s.attributes.get("unit_of_measurement"),
                    s.attributes.get("device_class"),
                    json.dumps(dict(s.attributes), default=str),
                )
                for s in hass.states.async_all()
            ]
        if "areas" in needed:
            out["areas"] = [
                (a.id, a.name, a.floor_id, a.icon, ",".join(sorted(a.aliases)))
                for a in ar.async_get(hass).async_list_areas()
            ]
        if "floors" in needed:
            out["floors"] = [
                (f.floor_id, f.name, f.level)
                for f in fr.async_get(hass).async_list_floors()
            ]
        if "labels" in needed:
            out["labels"] = [
                (lb.label_id, lb.name, lb.color, lb.description)
                for lb in lr.async_get(hass).async_list_labels()
            ]
        if "device_labels" in needed:
            out["device_labels"] = [
                (d.id, label) for d in dev_reg.devices.values() for label in d.labels
            ]
        if "entity_labels" in needed:
            out["entity_labels"] = [
                (e.entity_id, label)
                for e in ent_reg.entities.values()
                for label in e.labels
            ]
        if "config_entries" in needed:
            rows = []
            for e in entries.values():
                name, itype = _integration_info(hass, e.domain)
                rows.append(
                    (
                        e.entry_id, e.domain, e.title, e.state.value, e.source,
                        e.disabled_by.value if e.disabled_by else None,
                        name, itype,
                    )
                )
            out["config_entries"] = rows
        if needed & {"dashboards", "dashboard_references"}:
            dashes = await self.async_dashboards()
            out["dashboards"] = [
                (d.url_path, d.title, d.mode, int(d.require_admin),
                 int(d.show_in_sidebar))
                for d in dashes
            ]
            refs = []
            ent_ids = {e.entity_id: e.device_id for e in ent_reg.entities.values()}
            for d in dashes:
                for idx, path, title, tokens in d.views:
                    for tok in tokens:
                        if dev_reg.async_get(tok) is not None:
                            refs.append((d.url_path, idx, path, title, tok, None))
                        elif tok in ent_ids:
                            refs.append(
                                (d.url_path, idx, path, title, ent_ids[tok], tok)
                            )
            out["dashboard_references"] = refs
        if "automation_references" in needed:
            out["automation_references"] = self._automation_refs()
        if "relations" in needed:
            index = await self.async_relations_index()
            out["relations"] = [
                (r["device_id"], r["category"], r["title"], r["subtitle"],
                 r["link_type"], r["link_target"])
                for rels in index.values()
                for r in rels
            ]
        return out

    @callback
    def _automation_refs(self) -> list[tuple]:
        rows: list[tuple] = []
        ent_reg = er.async_get(self.hass)
        for source_domain in ("automation", "script"):
            try:
                if source_domain == "automation":
                    from homeassistant.components.automation import (  # noqa: PLC0415
                        devices_in_automation as devs,
                        entities_in_automation as ents,
                    )
                else:
                    from homeassistant.components.script import (  # noqa: PLC0415
                        devices_in_script as devs,
                        entities_in_script as ents,
                    )
            except ImportError:
                continue
            for state in self.hass.states.async_all(source_domain):
                for dev_id in devs(self.hass, state.entity_id):
                    rows.append((state.entity_id, source_domain, dev_id, None))
                for ent_id in ents(self.hass, state.entity_id):
                    reg = ent_reg.async_get(ent_id)
                    rows.append(
                        (state.entity_id, source_domain,
                         reg.device_id if reg else None, ent_id)
                    )
        return rows


def _entry_rel(entry, integration_name: str, itype: str, how: str) -> dict:
    category = "helpers" if itype == "helper" else "entries"
    return {
        "category": category,
        "title": entry.title or integration_name,
        "subtitle": f"{integration_name} · {how}",
        "link_type": "config_entry",
        "link_target": f"{entry.domain}:{entry.entry_id}",
    }
