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

from .compat import all_devices, device_entry_ids, device_primary_entry
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ENTITY_ID_RE = re.compile(r"\b([a-z0-9_]+\.[a-z0-9_]+)\b")
DEVICE_ID_RE = re.compile(r"\b([0-9a-f]{32})\b")
# State attributes that name the entities another entity acts on. Only these
# are read: other attributes mention entities in passing (last source etc.).
TARGET_ATTRIBUTES = ("entities", "entity_id")
MAX_USED_BY_NAMES = 5


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
        device_ids = {d.id for d in all_devices(dev_reg)}

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
        for device in all_devices(dev_reg):
            for entry_id in device_entry_ids(device):
                entry = entries.get(entry_id)
                if entry is None or entry.domain == DOMAIN:
                    continue
                name, itype = _integration_info(hass, entry.domain)
                add(device.id, _entry_rel(entry, name, itype, "Device integration"))

        # 2a. Entities owned by another integration whose state lists this
        #     device's entities as targets (e.g. Scheduler's switch.schedule_*
        #     entities: {"entities": ["climate.x"]}). The integration keeps
        #     these in its own storage, not in its config entry.
        used_by: dict[tuple[str, str], list[str]] = {}
        for state in hass.states.async_all():
            reg = ent_reg.async_get(state.entity_id)
            if reg is None or not reg.config_entry_id:
                continue
            entry = entries.get(reg.config_entry_id)
            if entry is None or entry.domain == DOMAIN:
                continue
            targets: set[str] = set()
            for key in TARGET_ATTRIBUTES:
                value = state.attributes.get(key)
                if isinstance(value, str):
                    targets.add(value)
                elif isinstance(value, (list, tuple)):
                    targets.update(v for v in value if isinstance(v, str))
            for dev_id in devices_for(targets):
                if entity_to_device.get(state.entity_id) == dev_id:
                    continue
                used_by.setdefault((dev_id, entry.entry_id), []).append(state.name)

        def used_by_text(dev_id: str, entry, integration_name: str) -> str | None:
            names = used_by.pop((dev_id, entry.entry_id), None)
            if not names:
                return None
            prefix = f"{integration_name} "
            short = sorted(
                {n[len(prefix):] if n.lower().startswith(prefix.lower()) else n for n in names},
                key=str.lower,
            )
            shown = ", ".join(short[:MAX_USED_BY_NAMES])
            more = len(short) - MAX_USED_BY_NAMES
            if more > 0:
                shown += f" +{more} more"
            return f"Used by {len(short)}: {shown}"

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
                if (dev := dev_reg.async_get(dev_id)) is None or entry.entry_id in device_entry_ids(dev):
                    continue
                # Linked through its settings already (helpers such as groups,
                # whose own entity also lists the members): keep the plain text.
                used_by.pop((dev_id, entry.entry_id), None)
                add(dev_id, _entry_rel(entry, name, itype, "Uses this device"))

        # 2b. The rest of 2a: integrations linked only through their entities.
        for dev_id, entry_id in list(used_by):
            entry = entries[entry_id]
            dev = dev_reg.async_get(dev_id)
            if dev is None or entry_id in device_entry_ids(dev):
                used_by.pop((dev_id, entry_id), None)
                continue
            name, itype = _integration_info(hass, entry.domain)
            how = used_by_text(dev_id, entry, name)
            if how:
                add(dev_id, _entry_rel(entry, name, itype, how))

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
                for d in all_devices(dev_reg)
            ]
        if "device_config_entries" in needed:
            out["device_config_entries"] = [
                (d.id, eid, domain_of(eid))
                for d in all_devices(dev_reg)
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
                (d.id, label) for d in all_devices(dev_reg) for label in d.labels
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
        if "automations" in needed:
            out["automations"] = self._automations()
        if "group_members" in needed:
            out["group_members"] = _group_member_rows(hass, ent_reg)
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
        """(source, domain, device_id, entity_id, part) for automations and scripts.

        Automations are split into trigger / condition / action parts with
        Home Assistant's own extractors on the validated config (blueprints
        included). Scripts are all "action".
        """
        rows: list[tuple] = []
        ent_reg = er.async_get(self.hass)

        def emit(source: str, domain: str, part: str, ents, devs) -> None:
            for dev_id in devs:
                rows.append((source, domain, dev_id, None, part))
            for ent_id in ents:
                reg = ent_reg.async_get(ent_id)
                rows.append(
                    (source, domain, reg.device_id if reg else None, ent_id, part)
                )

        for entity in self._component_entities("automation"):
            parts = _automation_parts(entity)
            if parts is None:  # internals changed: fall back to "any part"
                emit(entity.entity_id, "automation", "any",
                     getattr(entity, "referenced_entities", set()),
                     getattr(entity, "referenced_devices", set()))
                continue
            for part, (ents, devs) in parts.items():
                emit(entity.entity_id, "automation", part, ents, devs)
        for entity in self._component_entities("script"):
            emit(entity.entity_id, "script", "action",
                 getattr(entity, "referenced_entities", set()),
                 getattr(entity, "referenced_devices", set()))
        return rows

    @callback
    def _component_entities(self, domain: str) -> list:
        """Entity objects of the automation or script component."""
        try:
            if domain == "automation":
                from homeassistant.components.automation import (  # noqa: PLC0415
                    DATA_COMPONENT,
                )
            else:
                from homeassistant.components.script import (  # noqa: PLC0415
                    DATA_COMPONENT,
                )
        except ImportError:
            return []
        component = self.hass.data.get(DATA_COMPONENT)
        return list(component.entities) if component is not None else []

    @callback
    def _automations(self) -> list[tuple]:
        rows: list[tuple] = []
        for entity in self._component_entities("automation"):
            state = self.hass.states.get(entity.entity_id)
            attrs = state.attributes if state else {}
            config_id = attrs.get("id")
            rows.append(
                (
                    entity.entity_id,
                    state.name if state else entity.entity_id,
                    state.state if state else None,
                    {"on": "Active", "off": "Inactive"}.get(
                        state.state if state else "", "Unavailable"
                    ),
                    config_id,
                    f"/config/automation/edit/{config_id}" if config_id else None,
                    attrs.get("mode"),
                    str(attrs["last_triggered"]) if attrs.get("last_triggered") else None,
                )
            )
        return rows


def _automation_parts(entity) -> dict[str, tuple[set[str], set[str]]] | None:
    """Entities and devices referenced by each part of one automation."""
    try:
        from homeassistant.helpers import (  # noqa: PLC0415
            condition as condition_helper,
            trigger as trigger_helper,
        )

        triggers = getattr(entity, "_trigger_config")
        cond = getattr(entity, "_condition")
        t_ents: set[str] = set()
        t_devs: set[str] = set()
        for conf in triggers:
            t_ents |= set(trigger_helper.async_extract_entities(conf))
            t_devs |= set(trigger_helper.async_extract_devices(conf))
        c_ents: set[str] = set()
        c_devs: set[str] = set()
        for conf in (cond.config if cond is not None else []):
            c_ents |= set(condition_helper.async_extract_entities(conf))
            c_devs |= set(condition_helper.async_extract_devices(conf))
        script = entity.action_script
        return {
            "trigger": (t_ents, t_devs),
            "condition": (c_ents, c_devs),
            "action": (set(script.referenced_entities), set(script.referenced_devices)),
        }
    except Exception:  # noqa: BLE001 - private internals; caller falls back
        return None


def _entry_rel(entry, integration_name: str, itype: str, how: str) -> dict:
    category = "helpers" if itype == "helper" else "entries"
    return {
        "category": category,
        "title": entry.title or integration_name,
        "subtitle": f"{integration_name} · {how}",
        "link_type": "config_entry",
        "link_target": f"{entry.domain}:{entry.entry_id}",
    }


MAX_GROUP_DEPTH = 10


@callback
def _group_member_rows(hass: HomeAssistant, ent_reg: er.EntityRegistry) -> list[tuple]:
    """Every (group, member) pair, members of nested groups included.

    A group is any entity whose state lists members in its ``entity_id``
    attribute. Nesting is followed only through group helpers (platform
    ``group``): a Scheduler schedule or a ZHA group inside a group is a
    member, but what *it* lists is not. Each pair appears once, at the
    shallowest depth it is reached; loops (a group inside itself) are cut.
    """
    direct: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    meta: dict[str, tuple] = {}
    for state in hass.states.async_all():
        members = state.attributes.get("entity_id")
        if isinstance(members, str):
            members = [members]
        if not isinstance(members, (list, tuple)):
            continue
        direct[state.entity_id] = [m for m in members if isinstance(m, str)]
        names[state.entity_id] = state.name
        reg = ent_reg.async_get(state.entity_id)
        platform = reg.platform if reg else ("group" if state.domain == "group" else None)
        meta[state.entity_id] = (state.name, state.domain, platform)

    device_of: dict[str, str | None] = {}

    def device(entity_id: str) -> str | None:
        if entity_id not in device_of:
            reg = ent_reg.async_get(entity_id)
            device_of[entity_id] = reg.device_id if reg else None
        return device_of[entity_id]

    rows: list[tuple] = []
    for group, (name, domain, platform) in meta.items():
        seen = {group}
        frontier = [(group, m) for m in direct[group]]
        depth = 1
        while frontier and depth <= MAX_GROUP_DEPTH:
            nxt: list[tuple[str, str]] = []
            for via, member in frontier:
                if member in seen:
                    continue
                seen.add(member)
                rows.append((group, name, domain, platform, member, device(member),
                             depth, via, names.get(via)))
                if member in meta and meta[member][2] == "group":
                    nxt.extend((member, m) for m in direct[member])
            frontier = nxt
            depth += 1
    return rows
