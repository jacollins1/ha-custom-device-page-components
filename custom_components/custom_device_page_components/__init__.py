"""Custom Device Page Components.

Adds cards to Home Assistant device pages: Related helpers, integration
entries and dashboards; user-built query components; and links built-in
entities (color temperature, Zigbee bindings) to devices.

The device-page card is a frontend module this integration registers with
Home Assistant's frontend.add_extra_js_url, so nothing is written to
configuration.yaml.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import time

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

from .bindings import BindingsManager
from .const import (
    CONF_BINDINGS,
    CONF_COLOR_TEMP,
    CONF_COLOR_TEMP_KELVIN,
    CONF_COLOR_TEMP_MIREDS,
    CONF_COLOR_TEMP_UNIT,
    CONF_RELATED_COLUMN,
    DOMAIN,
    FRONTEND_FILE,
    ISSUE_ATTACH_FAILED,
    KIND_CT_KELVIN,
    KIND_CT_MIREDS,
    KINDS_BINDINGS,
    SIGNAL_ATTACH_REPORT,
    STATIC_URL,
    UNIT_MIREDS,
)
from .runtime import Runtime
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SENSOR]
FRONTEND_DIR = Path(__file__).parent / "www"
DATA_STATIC = f"{DOMAIN}_static_registered"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CDPCConfigEntry = ConfigEntry[Runtime]


def _module_hash() -> tuple[str | None, str | None]:
    """(actual sha256, expected sha256) of the frontend module."""
    module = FRONTEND_DIR / FRONTEND_FILE
    expected_file = FRONTEND_DIR / f"{FRONTEND_FILE}.sha256"
    try:
        actual = hashlib.sha256(module.read_bytes()).hexdigest()
    except OSError:
        actual = None
    try:
        expected = expected_file.read_text().split()[0]
    except (OSError, IndexError):
        expected = None
    return actual, expected


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the websocket commands once."""
    async_register_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CDPCConfigEntry) -> bool:
    """Set up the entry."""
    runtime = Runtime(hass, entry)
    entry.runtime_data = runtime
    runtime.snapshot.async_start()
    entry.async_on_unload(runtime.snapshot.async_stop)

    if entry.options.get(CONF_BINDINGS, True):
        runtime.bindings = BindingsManager(hass)
        await runtime.bindings.async_load()

    if not hass.data.get(DATA_STATIC):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.data[DATA_STATIC] = True
    await async_register_module(hass, runtime)

    _remove_disabled_builtins(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_register_module(hass: HomeAssistant, runtime: Runtime) -> bool:
    """Check the module file and (re)register it. False if it is broken."""
    actual, expected = await hass.async_add_executor_job(_module_hash)
    if runtime.module_url:
        try:
            remove_extra_js_url(hass, runtime.module_url)
        except KeyError:
            pass
        runtime.module_url = None
    if actual is None or (expected is not None and actual != expected):
        reason = (
            "The device-page module file is missing"
            if actual is None
            else "The device-page module file does not match this release"
        )
        runtime.async_attach_report(False, reason, HA_VERSION, None)
        return False
    # The hash changes with every release; the time part makes a browser
    # fetch it again after a manual reload.
    runtime.module_url = f"{STATIC_URL}/{FRONTEND_FILE}?v={actual[:10]}-{int(time.time())}"
    add_extra_js_url(hass, runtime.module_url)
    return True


@callback
def async_mark_reloaded(hass: HomeAssistant, runtime: Runtime) -> None:
    """After a manual reload, wait for a browser to report again."""
    runtime.attach.status = "reloaded"
    runtime.attach.reason = "Refresh a browser and open any device page to check"
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ATTACH_FAILED)
    async_dispatcher_send(hass, SIGNAL_ATTACH_REPORT)


@callback
def _remove_disabled_builtins(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete this integration's own entities for built-ins that are off.

    Only entities this config entry created are touched. The options flow
    warns and asks for confirmation before this can happen.
    """
    ent_reg = er.async_get(hass)
    drop: set[str] = set()
    if not entry.options.get(CONF_COLOR_TEMP_MIREDS, True):
        drop.add(KIND_CT_MIREDS)
    if not entry.options.get(CONF_COLOR_TEMP_KELVIN, True):
        drop.add(KIND_CT_KELVIN)
    if not entry.options.get(CONF_BINDINGS, True):
        drop |= set(KINDS_BINDINGS)
    if not drop:
        return
    for reg in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if (reg.unique_id or "").rsplit("_", 1)[-1] in drop:
            ent_reg.async_remove(reg.entity_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring entries made by older versions up to date."""
    if entry.version > 1:
        return False  # made by a newer version of this integration
    if entry.minor_version < 2:
        # 0.1.0-0.1.1: one Color temperature box in one unit. Now a mireds and
        # a kelvin box can be on together. The existing box keeps its entity
        # id and history: in kelvin installs it becomes the kelvin box. New
        # installs get both; existing ones keep the unit they chose, plus
        # kelvin when they had chosen mireds (Home Assistant's own unit).
        opts = dict(entry.options)
        enabled = bool(opts.pop(CONF_COLOR_TEMP, True))
        mireds = opts.pop(CONF_COLOR_TEMP_UNIT, None) == UNIT_MIREDS
        opts.setdefault(CONF_COLOR_TEMP_MIREDS, enabled and mireds)
        opts.setdefault(CONF_COLOR_TEMP_KELVIN, enabled)
        opts.setdefault(CONF_RELATED_COLUMN, 1)
        if not mireds:

            @callback
            def _to_kelvin(reg: er.RegistryEntry) -> dict[str, str] | None:
                if (reg.unique_id or "").endswith(f"_{KIND_CT_MIREDS}"):
                    return {"new_unique_id": f"{reg.unique_id}k"}
                return None

            await er.async_migrate_entries(hass, entry.entry_id, _to_kelvin)
        hass.config_entries.async_update_entry(entry, options=opts, minor_version=2)
        _LOGGER.debug("Migrated %s to 1.2", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CDPCConfigEntry) -> bool:
    """Unload the entry."""
    runtime = entry.runtime_data
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        if runtime.module_url:
            try:
                remove_extra_js_url(hass, runtime.module_url)
            except KeyError:
                pass
        if runtime.bindings:
            await runtime.bindings.async_unload()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete this integration's own stored data when it is removed."""
    from homeassistant.helpers.storage import Store  # noqa: PLC0415

    await Store(hass, 1, f"{DOMAIN}.bindings").async_remove()
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ATTACH_FAILED)
