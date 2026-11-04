"""Per-entry runtime: builds what the device page shows."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from . import query
from .actions import ActionRefused, action_level, async_run_action
from .const import (
    COLUMNS,
    CONF_RELATED,
    CONF_RELATED_COLUMN,
    CONF_RELATED_ORDER,
    DOMAIN,
    ISSUE_ATTACH_FAILED,
    SIGNAL_ATTACH_REPORT,
    SUBENTRY_COMPONENT,
    TESTED_HA_VERSION,
    VERSION,
)
from .filters import device_matches
from .snapshot import SnapshotManager

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .bindings import BindingsManager

_LOGGER = logging.getLogger(__name__)


@dataclass
class AttachState:
    """Last report from a browser about the device-page card."""

    status: str = "unknown"  # unknown / attached / failed
    reason: str | None = None
    ha_version: str | None = None
    module_version: str | None = None
    reported_at: str | None = None
    module_url: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


def _link(hass: HomeAssistant, kind: str | None, value: Any) -> dict | None:
    """Turn a link type and value into something the browser can follow."""
    if value in (None, "") or kind in (None, "none"):
        return None
    value = str(value)
    if kind == "entity":
        return {"kind": "more_info", "entity_id": value}
    if kind == "device":
        return {"kind": "url", "url": f"/config/devices/device/{value}"}
    if kind == "area":
        return {"kind": "url", "url": f"/config/areas/area/{value}"}
    if kind == "config_entry":
        if ":" in value:
            domain, _, entry_id = value.partition(":")
        else:
            entry = hass.config_entries.async_get_entry(value)
            if entry is None:
                return None
            domain, entry_id = entry.domain, value
        return {
            "kind": "url",
            "url": f"/config/integrations/integration/{domain}#config_entry={entry_id}",
        }
    if kind == "url":
        # Only paths inside Home Assistant: no external or script URLs.
        if value.startswith("/") and not value.startswith("//"):
            return {"kind": "url", "url": value}
        return None
    return None


def row_key(row: list[Any]) -> str:
    """Fingerprint of a row, used to check an action targets what was shown."""
    return hashlib.sha1(
        json.dumps(row, default=str).encode(), usedforsecurity=False
    ).hexdigest()[:16]


class Runtime:
    """Holds everything the device page and the platforms need."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.snapshot = SnapshotManager(hass)
        self.bindings: BindingsManager | None = None
        self.attach = AttachState()
        self.module_url: str | None = None

    # ------------------------------------------------------------------
    @property
    def components(self) -> list[tuple[str, dict[str, Any]]]:
        """(subentry_id, spec) for every user-defined component."""
        return [
            (sub.subentry_id, dict(sub.data))
            for sub in self.entry.subentries.values()
            if sub.subentry_type == SUBENTRY_COMPONENT
        ]

    async def async_run_spec(
        self, spec: dict[str, Any], device_id: str | None
    ) -> query.QueryResult:
        """Run a spec for a device (None = no device context)."""
        ctx = (
            self.snapshot.device_context(device_id) if device_id else None
        ) or {k: None for k in query.DEVICE_CONTEXT_FIELDS}
        needed = set(query.aliases_in(spec).values())
        tables = await self.snapshot.async_tables(needed)
        return await self.hass.async_add_executor_job(query.run, spec, tables, ctx)

    async def async_device_payload(self, device_id: str) -> dict[str, Any]:
        """Everything the device page card renders for one device."""
        device = dr.async_get(self.hass).async_get(device_id)
        comps: list[dict[str, Any]] = []
        if device is None:
            return {"version": VERSION, "components": comps}
        opts = self.entry.options
        if opts.get(CONF_RELATED, True):
            related = await self.snapshot.async_related(device_id)
            for items in related.values():
                for rel in items:
                    rel["link"] = _link(self.hass, rel["link_type"], rel["link_target"])
            comps.append(
                {
                    "id": "related",
                    "type": "related",
                    "title": "Related helpers, integrations and dashboards",
                    "order": int(opts.get(CONF_RELATED_ORDER, 0)),
                    "column": _column(opts.get(CONF_RELATED_COLUMN, 1)),
                    "related": related,
                }
            )
        available = self.snapshot.device_available(device_id)
        for sub_id, spec in self.components:
            if not spec.get("enabled", True):
                continue
            if not device_matches(self.hass, device, spec.get("device_filter"), available):
                continue
            comps.append(await self._component_payload(sub_id, spec, device_id))
        comps.sort(key=lambda c: (c["order"], c["title"].lower()))
        return {"version": VERSION, "components": comps}

    async def _component_payload(
        self, sub_id: str, spec: dict[str, Any], device_id: str
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": sub_id,
            "type": "query",
            "title": spec.get("title") or spec.get("name") or "Component",
            "order": int(spec.get("order", 10)),
            "column": _column(spec.get("column", 1)),
            "style": spec.get("style", "table"),
            "empty_text": spec.get("empty_text") or "Nothing to show",
            "labels": [],
            "rows": [],
            "actions": [
                {
                    "index": i,
                    "label": a.get("label") or a.get("action"),
                    "confirm": bool(a.get("confirm"))
                    or action_level(a.get("action") or "") == "confirm",
                    "confirm_text": a.get("confirm_text")
                    or f"Run {a.get('action')}?",
                }
                for i, a in enumerate(spec.get("actions") or [])
                # The policy is re-checked on every load, so an action that
                # a newer version blocks disappears from the page.
                if action_level(a.get("action") or "") != "blocked"
            ],
            "truncated": False,
            "error": None,
        }
        try:
            result = await self.async_run_spec(spec, device_id)
        except query.QueryError as err:
            out["error"] = str(err)
            return out
        shown = len(spec.get("columns") or []) or len(result.columns)
        hidden_refs = result.columns
        out["labels"] = result.labels[:shown]
        link_cfg = spec.get("row_link") or {}
        for row in result.rows:
            values = dict(zip(hidden_refs, row, strict=False))
            out["rows"].append(
                {
                    "cells": row[:shown],
                    "key": row_key(row),
                    "link": _link(
                        self.hass, link_cfg.get("type"), values.get(link_cfg.get("column"))
                    ),
                }
            )
        out["truncated"] = result.truncated
        return out

    async def async_run_row_action(
        self,
        device_id: str,
        component_id: str,
        action_index: int,
        key: str,
        confirmed: bool,
        user_id: str,
    ) -> None:
        """Re-run the query, find the row by key and run the action."""
        spec = next((s for sid, s in self.components if sid == component_id), None)
        if spec is None:
            raise ActionRefused("Component not found")
        actions = spec.get("actions") or []
        if not 0 <= action_index < len(actions):
            raise ActionRefused("Action not found")
        action_cfg = actions[action_index]
        result = await self.async_run_spec(spec, device_id)
        for row in result.rows:
            if row_key(row) == key:
                values = dict(zip(result.columns, row, strict=False))
                await async_run_action(
                    self.hass,
                    action_cfg,
                    values.get(action_cfg.get("target_column")),
                    user_id,
                    confirmed,
                )
                return
        raise ActionRefused("That row has changed; refresh the page and try again")

    # ------------------------------------------------------------------
    @callback
    def async_attach_report(
        self, ok: bool, reason: str | None, ha_version: str | None, module: str | None
    ) -> None:
        """Record what a browser saw and raise or clear the repair issue."""
        state = self.attach
        state.status = "attached" if ok else "failed"
        state.reason = reason
        state.ha_version = ha_version
        state.module_version = module
        state.reported_at = dt_util.utcnow().isoformat()
        state.history = (
            [{"at": state.reported_at, "status": state.status, "reason": reason}]
            + state.history
        )[:10]
        if ok:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ATTACH_FAILED)
        else:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_ATTACH_FAILED,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_ATTACH_FAILED,
                translation_placeholders={
                    "ha_version": ha_version or "unknown",
                    "tested": TESTED_HA_VERSION,
                    "reason": reason or "unknown",
                },
            )
        async_dispatcher_send(self.hass, SIGNAL_ATTACH_REPORT)


def _column(value: Any) -> int:
    """Device page column 1-3 (anything else = 1)."""
    try:
        col = int(value)
    except (TypeError, ValueError):
        return 1
    return col if col in COLUMNS else 1
