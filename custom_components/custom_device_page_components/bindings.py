"""Zigbee binding tables (ZHA only).

Reads a device's binding table with ZDO Mgmt_Bind_req through ZHA's zigpy
application. Read-only: nothing is bound or unbound. Reads only happen on a
button press, one device at a time.

This part uses ZHA's internal gateway object, which is not a public API. If
a ZHA update changes it, the Zigbee binding entities report "ZHA not
available" and the rest of the integration keeps working.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .compat import all_devices
from .const import DOMAIN, SIGNAL_BINDINGS_UPDATED

_LOGGER = logging.getLogger(__name__)

PAGE_TIMEOUT = 15
READ_ALL_GAP = 3
WAKE_POLL = 10
WAKE_GIVE_UP = 24 * 3600

ST_NOT_READ = "Not read yet"
ST_READING = "Reading…"
ST_WAITING = "Waiting for device to wake"
ST_UNSUPPORTED = "Device can't report its bindings"
ST_NO_REPLY = "Read failed: No reply"
ST_OK = "OK"
ST_NO_ZHA = "ZHA not available"


def zha_ieee(device: dr.DeviceEntry) -> str | None:
    """IEEE address of a ZHA device, from its registry identifiers."""
    for domain, ident in device.identifiers:
        if domain == "zha":
            return ident
    return None


def _app(hass: HomeAssistant):
    """ZHA's zigpy application, or None."""
    try:
        from homeassistant.components.zha.helpers import (  # noqa: PLC0415
            get_zha_gateway,
        )

        return get_zha_gateway(hass).application_controller
    except Exception:  # noqa: BLE001 - ZHA not loaded or internals changed
        return None


def _cluster_name(cluster_id: int) -> str:
    try:
        from zigpy.zcl import Cluster  # noqa: PLC0415

        cls = Cluster._registry.get(cluster_id)  # noqa: SLF001
        if cls is not None:
            return f"{cls.ep_attribute or cls.__name__} (0x{cluster_id:04X})"
    except Exception:  # noqa: BLE001
        pass
    return f"0x{cluster_id:04X}"


class BindingsManager:
    """Stores binding tables and runs reads."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.bindings")
        self.tables: dict[str, dict[str, Any]] = {}
        self.status: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._waiters: dict[str, asyncio.Task] = {}
        self._read_all_task: asyncio.Task | None = None

    async def async_load(self) -> None:
        """Load stored tables."""
        data = await self._store.async_load() or {}
        self.tables = data.get("tables", {})

    async def async_unload(self) -> None:
        """Cancel work and save."""
        for task in list(self._waiters.values()):
            task.cancel()
        if self._read_all_task:
            self._read_all_task.cancel()
        await self._store.async_save({"tables": self.tables})

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"tables": self.tables}, 5)

    @callback
    def _changed(self, ieee: str | None = None) -> None:
        async_dispatcher_send(self.hass, SIGNAL_BINDINGS_UPDATED, ieee)

    @property
    def zha_available(self) -> bool:
        """True when ZHA's application can be reached."""
        return _app(self.hass) is not None

    def status_for(self, ieee: str) -> str:
        """Human status for a device."""
        if not self.zha_available:
            return ST_NO_ZHA
        if ieee in self.status:
            return self.status[ieee]
        table = self.tables.get(ieee)
        return table["status"] if table else ST_NOT_READ

    # ------------------------------------------------------------------
    async def async_request_read(self, ieee: str) -> None:
        """Read now, or queue until a sleepy device wakes."""
        app = _app(self.hass)
        if app is None:
            return
        from zigpy.types import EUI64  # noqa: PLC0415

        try:
            dev = app.get_device(ieee=EUI64.convert(ieee))
        except KeyError:
            return
        nd = dev.node_desc
        if nd is not None and nd.is_end_device and not nd.is_receiver_on_when_idle:
            if ieee not in self._waiters:
                self.status[ieee] = ST_WAITING
                self._changed(ieee)
                self._waiters[ieee] = self.hass.async_create_background_task(
                    self._wait_and_read(dev, ieee), f"{DOMAIN} wait {ieee}"
                )
            return
        await self._read(dev, ieee)

    async def _wait_and_read(self, dev, ieee: str) -> None:
        start = dev.last_seen
        waited = 0
        try:
            while waited < WAKE_GIVE_UP:
                await asyncio.sleep(WAKE_POLL)
                waited += WAKE_POLL
                if dev.last_seen != start:
                    await self._read(dev, ieee)
                    return
            self.status.pop(ieee, None)
            self._changed(ieee)
        finally:
            self._waiters.pop(ieee, None)

    async def _read(self, dev, ieee: str) -> None:
        from zigpy.zdo.types import Status  # noqa: PLC0415

        async with self._lock:
            self.status[ieee] = ST_READING
            self._changed(ieee)
            entries: list[dict[str, Any]] = []
            status = ST_OK
            index = 0
            try:
                while True:
                    async with asyncio.timeout(PAGE_TIMEOUT):
                        rsp = await dev.zdo.Mgmt_Bind_req(index)
                    st, total, _start, table = rsp[0], rsp[1], rsp[2], rsp[3]
                    if st == Status.NOT_SUPPORTED:
                        status = ST_UNSUPPORTED
                        break
                    if st != Status.SUCCESS:
                        status = f"Read failed: {st.name}"
                        break
                    for b in table:
                        dst = b.DstAddress
                        if dst.addrmode == 0x01:
                            entries.append(
                                {"src_ep": b.SrcEndpoint, "cluster": b.ClusterId,
                                 "dst_type": "group", "group_id": int(dst.nwk)}
                            )
                        else:
                            entries.append(
                                {"src_ep": b.SrcEndpoint, "cluster": b.ClusterId,
                                 "dst_type": "device", "dst_ieee": str(dst.ieee),
                                 "dst_ep": dst.endpoint}
                            )
                    index += len(table)
                    if not table or index >= total:
                        break
            except (TimeoutError, Exception) as err:  # noqa: BLE001
                _LOGGER.debug("Binding read of %s failed: %s", ieee, err)
                status = ST_NO_REPLY
            self.status.pop(ieee, None)
            prev = self.tables.get(ieee, {})
            self.tables[ieee] = {
                "status": status,
                "read_at": dt_util.utcnow().isoformat(),
                # Keep the last good table if this read failed.
                "entries": entries if status == ST_OK else prev.get("entries", []),
            }
            self._save()
            self._changed(None)  # reverse views of other devices change too

    @callback
    def async_read_all(self) -> bool:
        """Read every mains-powered device, one at a time. False if running."""
        if self._read_all_task and not self._read_all_task.done():
            return False
        self._read_all_task = self.hass.async_create_background_task(
            self._read_all(), f"{DOMAIN} read all bindings"
        )
        return True

    async def _read_all(self) -> None:
        app = _app(self.hass)
        if app is None:
            return
        for dev in list(app.devices.values()):
            nd = dev.node_desc
            if nd is None or nd.is_coordinator:
                continue
            if nd.is_end_device and not nd.is_receiver_on_when_idle:
                continue
            await self._read(dev, str(dev.ieee))
            await asyncio.sleep(READ_ALL_GAP)

    # ------------------------------------------------------------------
    def _names(self) -> tuple[dict[str, str], dict[int, str], str | None]:
        dev_reg = dr.async_get(self.hass)
        names: dict[str, str] = {}
        for device in all_devices(dev_reg):
            if ieee := zha_ieee(device):
                names[ieee] = device.name_by_user or device.name or ieee
        groups: dict[int, str] = {}
        coordinator = None
        if app := _app(self.hass):
            for gid, group in app.groups.items():
                groups[int(gid)] = group.name or f"Group 0x{int(gid):04X}"
            coordinator = str(app.state.node_info.ieee)
        return names, groups, coordinator

    def bound_to(self, ieee: str) -> dict[str, Any]:
        """Destinations in this device's own table."""
        names, groups, coordinator = self._names()
        table = self.tables.get(ieee)
        items, reporting = [], []
        for e in (table or {}).get("entries", []):
            cluster = _cluster_name(e["cluster"])
            if e["dst_type"] == "group":
                gid = e["group_id"]
                items.append(
                    f"{groups.get(gid, f'Group 0x{gid:04X}')} ← ep{e['src_ep']} {cluster}"
                )
            elif e["dst_ieee"] == coordinator:
                reporting.append(f"ep{e['src_ep']} {cluster}")
            else:
                dst = names.get(e["dst_ieee"], e["dst_ieee"])
                items.append(f"{dst} ep{e['dst_ep']} ← ep{e['src_ep']} {cluster}")
        return {
            "items": items,
            "coordinator_reporting": reporting,
            "read_at": (table or {}).get("read_at"),
        }

    def bound_from(self, ieee: str) -> dict[str, Any]:
        """Other devices whose read tables point at this device."""
        names, groups, _ = self._names()
        member_groups: set[int] = set()
        if app := _app(self.hass):
            for gid, group in app.groups.items():
                if any(str(m[0]) == ieee for m in group.members):
                    member_groups.add(int(gid))
        items = []
        for src, table in self.tables.items():
            if src == ieee:
                continue
            for e in table.get("entries", []):
                cluster = _cluster_name(e["cluster"])
                src_name = names.get(src, src)
                if e["dst_type"] == "device" and e["dst_ieee"] == ieee:
                    items.append(f"{src_name} ep{e['src_ep']} {cluster}")
                elif e["dst_type"] == "group" and e["group_id"] in member_groups:
                    gname = groups.get(e["group_id"], str(e["group_id"]))
                    items.append(f"{src_name} via {gname} {cluster}")
        return {"items": items, "tables_read": len(self.tables)}
