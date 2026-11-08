"""Integration tests against a real Home Assistant core."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.custom_device_page_components.actions import ActionRefused
from custom_components.custom_device_page_components.const import DOMAIN

# Options as 0.1.0/0.1.1 stored them (one box, one unit): the fixture's entry
# goes through the 1.1 -> 1.2 migration on setup.
OPTS = {
    "related_enabled": True, "related_order": 0, "color_temp_enabled": True,
    "color_temp_unit": "mireds", "color_temp_models": [], "bindings_enabled": False,
}
# The options form today.
NEW_OPTS = {
    "related_enabled": True, "related_order": 0, "related_column": "1",
    "color_temp_kelvin_enabled": True, "color_temp_mireds_enabled": True,
    "color_temp_models": [], "bindings_enabled": False,
}

COMPONENT = {
    "name": "Lights here", "title": "Lights on this device", "style": "table", "order": 5,
    "enabled": True, "empty_text": "", "source": {"table": "entities", "alias": "t0"},
    "joins": [{"type": "left", "table": "states", "alias": "t1", "left": "t0.entity_id", "right": "t1.entity_id"}],
    "where": {"match": "all", "conditions": [
        {"column": "t0.device_id", "op": "eq", "value_source": "device.id", "value": ""},
        {"column": "t0.domain", "op": "eq", "value_source": "fixed", "value": "light"}]},
    "group_by": [], "aggregates": [], "order_by": [{"column": "t0.entity_id", "direction": "asc"}],
    "limit": 10, "columns": ["t0.entity_id", "t1.state"], "labels": {},
    "row_link": {"type": "entity", "column": "t0.entity_id"},
    "actions": [
        {"label": "Toggle", "action": "light.toggle", "target_column": "t0.entity_id", "target_kind": "entity_id", "data": {}, "confirm": False, "confirm_text": ""},
        {"label": "Restart HA", "action": "homeassistant.restart", "target_column": "t0.entity_id", "target_kind": "entity_id", "data": {}, "confirm": False, "confirm_text": ""},
    ],
    "device_filter": {"types": [], "statuses": [], "integrations": [], "areas": []},
}


@pytest.fixture
async def setup(hass: HomeAssistant):
    """A light device, a group helper entry naming it, and the integration."""
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, "websocket_api", {})
    hass.config.components.add("frontend")
    hass.data.setdefault("frontend_extra_module_url", set())

    other = MockConfigEntry(domain="zha", title="Zigbee")
    other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("zha", "00:11")},
        manufacturer="Signify", model="LCB002", name="Kitchen Overhead 1",
    )
    ent = er.async_get(hass).async_get_or_create(
        "light", "zha", "bulb1", device_id=dev.id, config_entry=other,
        suggested_object_id="kitchen_1",
        capabilities={"supported_color_modes": ["color_temp", "xy"],
                      "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6535},
    )
    hass.states.async_set(ent.entity_id, "on", {"color_mode": "color_temp", "color_temp_kelvin": 2702})
    helper = MockConfigEntry(domain="adaptive_lighting", title="Kitchen AL",
                             options={"lights": [ent.entity_id]})
    helper.add_to_hass(hass)

    entry = MockConfigEntry(
        domain=DOMAIN, title="Custom Device Page Components", options=OPTS,
        subentries_data=[ConfigSubentryData(data=COMPONENT, subentry_type="component",
                                            title="Lights here", unique_id=None)],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.custom_device_page_components.add_extra_js_url") as add_js:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return {"entry": entry, "dev": dev, "ent": ent, "helper": helper, "add_js": add_js}


async def test_module_registered(hass, setup):
    url = setup["add_js"].call_args[0][1]
    assert url.startswith("/custom_device_page_components_static/cdpc.js?v=")


async def test_color_temp_boxes_on_device(hass, setup):
    """A mireds and a kelvin box, both showing the light's temperature."""
    reg = er.async_get(hass)
    num = {e.unique_id.rsplit("_", 1)[-1]: e
           for e in er.async_entries_for_device(reg, setup["dev"].id) if e.domain == "number"}
    assert set(num) == {"colortemp", "colortempk"}
    assert all(e.config_entry_id == setup["entry"].entry_id for e in num.values())
    mired = hass.states.get(num["colortemp"].entity_id)
    kelvin = hass.states.get(num["colortempk"].entity_id)
    assert mired.state == "370"  # floor(1e6 / 2702)
    assert mired.attributes["min"] == 153 and mired.attributes["max"] == 500
    assert mired.attributes["unit_of_measurement"] == "mired"
    assert mired.attributes["friendly_name"].endswith("Color temperature")
    assert kelvin.state == "2702"
    assert kelvin.attributes["min"] == 2000 and kelvin.attributes["max"] == 6535
    assert kelvin.attributes["unit_of_measurement"] == "K"
    assert kelvin.attributes["friendly_name"].endswith("Color temperature (kelvin)")
    assert num["colortempk"].entity_id.endswith("_color_temperature_kelvin")
    # ZHA stays the only integration on the device
    assert dr.async_get(hass).async_get(setup["dev"].id).config_entry_id == setup["dev"].config_entry_id

    calls = []
    hass.services.async_register("light", "turn_on", lambda c: calls.append(c))
    await hass.services.async_call("number", "set_value", {"entity_id": num["colortemp"].entity_id, "value": 370}, blocking=True)
    assert calls[-1].data["color_temp_kelvin"] == 2702
    await hass.services.async_call("number", "set_value", {"entity_id": num["colortempk"].entity_id, "value": 3000}, blocking=True)
    assert calls[-1].data["color_temp_kelvin"] == 3000


async def test_migration_from_single_unit(hass, setup):
    """A 0.1.1 mireds entry keeps its box and gains the kelvin one."""
    entry = setup["entry"]
    assert entry.minor_version == 2
    assert "color_temp_enabled" not in entry.options and "color_temp_unit" not in entry.options
    assert entry.options["color_temp_mireds_enabled"] is True
    assert entry.options["color_temp_kelvin_enabled"] is True
    assert entry.options["related_column"] == 1


async def test_migration_keeps_kelvin_box(hass):
    """A 0.1.1 kelvin entry: the existing box becomes the kelvin box (same entity id)."""
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, "websocket_api", {})
    hass.config.components.add("frontend")
    hass.data.setdefault("frontend_extra_module_url", set())
    other = MockConfigEntry(domain="zha", title="Zigbee")
    other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("zha", "00:22")}, model="LCB002", name="Bulb",
    )
    light = er.async_get(hass).async_get_or_create(
        "light", "zha", "bulb2", device_id=dev.id, config_entry=other,
        capabilities={"supported_color_modes": ["color_temp"],
                      "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500},
    )
    hass.states.async_set(light.entity_id, "on", {"color_mode": "color_temp", "color_temp_kelvin": 4000})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Custom Device Page Components",
        options={**OPTS, "color_temp_unit": "kelvin"},
    )
    entry.add_to_hass(hass)
    old = er.async_get(hass).async_get_or_create(
        "number", DOMAIN, f"{light.id}_colortemp", config_entry=entry,
        suggested_object_id="bulb_color_temperature",
    )
    with patch("custom_components.custom_device_page_components.add_extra_js_url"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    assert entry.options["color_temp_kelvin_enabled"] is True
    assert entry.options["color_temp_mireds_enabled"] is False
    mine = [e for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.domain == "number"]
    assert [(e.entity_id, e.unique_id) for e in mine] == [(old.entity_id, f"{light.id}_colortempk")]
    assert hass.states.get(old.entity_id).state == "4000"


async def test_device_payload(hass, setup, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    res = (await client.receive_json())["result"]
    ids = [c["id"] for c in res["components"]]
    assert ids[0] == "related"
    # components saved before 0.1.2 have no column: they stay in column 1
    assert [c["column"] for c in res["components"]] == [1] * len(ids)
    related = res["components"][0]["related"]
    titles = {r["title"] for r in related["entries"]}
    assert {"Zigbee", "Kitchen AL"} <= titles
    al = next(r for r in related["entries"] if r["title"] == "Kitchen AL")
    assert al["link"]["url"] == f"/config/integrations/integration/adaptive_lighting#config_entry={setup['helper'].entry_id}"
    comp = res["components"][1]
    assert comp["rows"][0]["cells"] == [setup["ent"].entity_id, "on"]
    assert comp["rows"][0]["link"] == {"kind": "more_info", "entity_id": setup["ent"].entity_id}
    # blocked action is not offered
    assert [a["label"] for a in comp["actions"]] == ["Toggle"]


async def test_row_actions(hass, setup, hass_ws_client):
    calls: list[ServiceCall] = []
    hass.services.async_register("light", "toggle", lambda c: calls.append(c))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    res = (await client.receive_json())["result"]
    comp = res["components"][1]
    key = comp["rows"][0]["key"]
    base = {"type": f"{DOMAIN}/run_action", "device_id": setup["dev"].id, "component_id": comp["id"], "row_key": key}
    await client.send_json_auto_id({**base, "action_index": 0})
    assert (await client.receive_json())["success"]
    assert calls[0].data["entity_id"] in (setup["ent"].entity_id, [setup["ent"].entity_id])
    assert calls[0].context.user_id is not None
    # Blocked even if the browser asks for it directly
    await client.send_json_auto_id({**base, "action_index": 1, "confirmed": True})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "refused"
    # A forged row key is refused
    await client.send_json_auto_id({**base, "action_index": 0, "row_key": "forged"})
    assert not (await client.receive_json())["success"]


async def test_non_admin_refused(hass, setup, hass_ws_client, hass_read_only_access_token):
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"


async def test_attach_report_issue(hass, setup, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/attach_report", "ok": False, "reason": "x", "ha_version": "2027.1.0"})
    await client.receive_json()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "attach_failed")
    card = [s for s in hass.states.async_all("sensor") if s.entity_id.endswith("device_page_card")][0]
    assert card.state == "failed"
    await client.send_json_auto_id({"type": f"{DOMAIN}/attach_report", "ok": True})
    await client.receive_json()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "attach_failed") is None


async def test_device_filter(hass, setup, hass_ws_client):
    entry = setup["entry"]
    sub = next(iter(entry.subentries.values()))
    data = {**sub.data, "device_filter": {"types": [], "statuses": [], "integrations": ["hue"], "areas": []}}
    hass.config_entries.async_update_subentry(entry, sub, data=data)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    res = (await client.receive_json())["result"]
    assert [c["id"] for c in res["components"]] == ["related"]


async def test_builder_flow(hass, setup):
    entry = setup["entry"]
    mgr = hass.config_entries.subentries
    r = await mgr.async_init((entry.entry_id, "component"), context={"source": "user"})
    assert r["step_id"] == "basics"
    r = await mgr.async_configure(r["flow_id"], {"name": "Areas", "style": "list", "column": "3", "order": 3, "enabled": True})
    assert r["type"] == "menu"
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "source"})
    r = await mgr.async_configure(r["flow_id"], {"table": "devices"})
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "joins"})
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "add_join"})
    r = await mgr.async_configure(r["flow_id"], {"type": "inner", "table": "areas"})
    r = await mgr.async_configure(r["flow_id"], {"left": "t0.area_id", "right": "t1.area_id"})
    assert r["type"] == "menu"
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "menu"})
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "columns"})
    nine = [f"t0.{c}" for c in ("id", "display_name", "manufacturer", "model", "area_id", "name", "name_by_user", "model_id", "sw_version")]
    r = await mgr.async_configure(r["flow_id"], {"columns": nine, "link_type": "area", "link_column": "t0.area_id"})
    assert r["step_id"] == "columns" and r["errors"] == {"columns": "too_many_columns"}
    r = await mgr.async_configure(r["flow_id"], {"columns": ["t1.name"], "link_type": "area", "link_column": "t0.area_id"})
    # changing the source table warns and needs a confirmation
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "source"})
    r = await mgr.async_configure(r["flow_id"], {"table": "entities"})
    assert r["step_id"] == "source_confirm"
    r = await mgr.async_configure(r["flow_id"], {"confirm": False})
    assert r["type"] == "menu"
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "preview"})
    r = await mgr.async_configure(r["flow_id"], {"device": setup["dev"].id})
    assert r["step_id"] == "preview"
    r = await mgr.async_configure(r["flow_id"], {"done": True})
    r = await mgr.async_configure(r["flow_id"], {"next_step_id": "save"})
    r = await mgr.async_configure(r["flow_id"], {})
    assert r["type"] == "create_entry"
    assert len(entry.subentries) == 2
    new = next(s for s in entry.subentries.values() if s.title == "Areas")
    assert new.data["column"] == 3


async def test_options_warn_before_delete(hass, setup):
    entry = setup["entry"]
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {**NEW_OPTS, "color_temp_mireds_enabled": False})
    assert r["step_id"] == "confirm_remove"
    assert r["description_placeholders"]["count"] == "1"
    assert "_color_temperature`" in r["description_placeholders"]["entities"]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"confirm": False})
    assert r["errors"] == {"base": "confirm_required"}
    with patch("custom_components.custom_device_page_components.add_extra_js_url"):
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"confirm": True})
        await hass.async_block_till_done()
    assert r["type"] == "create_entry"
    reg = er.async_get(hass)
    left = [e.unique_id.rsplit("_", 1)[-1] for e in er.async_entries_for_device(reg, setup["dev"].id) if e.domain == "number"]
    assert left == ["colortempk"]  # only the mireds box was removed
    assert entry.options["related_column"] == 1


async def test_related_dashboards_and_yaml_groups(hass, setup, hass_ws_client, hass_storage):
    ent = setup["ent"].entity_id
    hass.states.async_set("light.kitchen_group", "on", {"entity_id": [ent], "friendly_name": "Kitchen group"})

    class FakeDash:
        config = {"title": "Home", "require_admin": True, "url_path": "home-dash"}
        mode = "storage"

        async def async_load(self, force):
            return {"views": [{"title": "Rooms", "path": "rooms", "cards": [{"type": "tile", "entity": ent}]},
                              {"title": "Other", "cards": []}]}

    from homeassistant.components.lovelace.const import LOVELACE_DATA

    class FakeData:
        dashboards = {"home-dash": FakeDash()}

    hass.data[LOVELACE_DATA] = FakeData()
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    related = (await client.receive_json())["result"]["components"][0]["related"]
    assert related["dashboards"][0]["link"]["url"] == "/home-dash/rooms"
    assert related["dashboards"][0]["subtitle"] == "Admin only"
    grp = [r for r in related["helpers"] if r["title"] == "Kitchen group"]
    assert grp and grp[0]["link"] == {"kind": "more_info", "entity_id": "light.kitchen_group"}


async def test_bindings_reader(hass):
    """Binding table read with a fake zigpy application."""
    import zigpy.types as t
    import zigpy.zdo.types as zdo_t

    from custom_components.custom_device_page_components.bindings import BindingsManager, ST_OK

    sw_ieee = t.EUI64.convert("00:00:00:00:00:00:00:01")
    coord = t.EUI64.convert("00:00:00:00:00:00:00:00")

    class FakeZDO:
        async def Mgmt_Bind_req(self, index):
            b1 = zdo_t.Binding(SrcAddress=sw_ieee, SrcEndpoint=1, ClusterId=6,
                               DstAddress=zdo_t.MultiAddress(addrmode=1, nwk=5))
            b2 = zdo_t.Binding(SrcAddress=sw_ieee, SrcEndpoint=1, ClusterId=0x0702,
                               DstAddress=zdo_t.MultiAddress(addrmode=3, ieee=coord, endpoint=1))
            return [zdo_t.Status.SUCCESS, 2, 0, [b1, b2]]

    class ND:
        is_end_device = False
        is_receiver_on_when_idle = True
        is_coordinator = False

    class Dev:
        zdo = FakeZDO()
        node_desc = ND()
        ieee = sw_ieee
        last_seen = 0

    class Group:
        name = "Dining bulbs"
        members = {}

    class App:
        devices = {sw_ieee: Dev()}
        groups = {5: Group()}

        class state:
            class node_info:
                ieee = coord

        def get_device(self, ieee):
            return self.devices[ieee]

    mgr = BindingsManager(hass)
    with patch("custom_components.custom_device_page_components.bindings._app", return_value=App()):
        await mgr.async_request_read(str(sw_ieee))
        assert mgr.status_for(str(sw_ieee)) == ST_OK
        out = mgr.bound_to(str(sw_ieee))
    assert out["items"] == ["Dining bulbs ← ep1 on_off (0x0006)"]
    assert out["coordinator_reporting"] == ["ep1 smartenergy_metering (0x0702)"]


def test_zha_ieee_ignores_group_devices():
    """2026.11 ZHA group devices carry a synthetic identifier, not an IEEE."""
    from types import SimpleNamespace

    from custom_components.custom_device_page_components.bindings import zha_ieee

    assert zha_ieee(SimpleNamespace(identifiers={("zha", "00:17:88:01:0e:d4:f6:21")})) == "00:17:88:01:0e:d4:f6:21"
    assert zha_ieee(SimpleNamespace(identifiers={("zha", "01KXYZ_group_0005")})) is None
    assert zha_ieee(SimpleNamespace(identifiers={("hue", "00:17:88:01:0e:d4:f6:21")})) is None


async def test_stale_group_binding_entities_removed(hass, setup):
    """Binding entities an older version made for a ZHA group device go on reload."""
    entry = setup["entry"]
    reg = er.async_get(hass)
    stale = reg.async_get_or_create("sensor", DOMAIN, "01KXYZ_group_0005_boundto", config_entry=entry)
    real = reg.async_get_or_create("sensor", DOMAIN, "00:11:22:33:44:55:66:77_boundto", config_entry=entry)
    with patch("custom_components.custom_device_page_components.add_extra_js_url"):
        # bindings on, so only the group-device entity is stale
        hass.config_entries.async_update_entry(entry, options={**entry.options, "bindings_enabled": True})
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert reg.async_get(stale.entity_id) is None
    assert reg.async_get(real.entity_id) is not None


async def test_color_temp_box_follows_light_to_new_device(hass, setup):
    """A light moved to another device (2026.11 ZHA groups) takes its boxes along."""
    reg = er.async_get(hass)
    other = MockConfigEntry(domain="zha", title="Zigbee 2")
    other.add_to_hass(hass)
    new_dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("zha", "grp_1")}, name="Group device",
    )
    reg.async_update_entity(setup["ent"].entity_id, device_id=new_dev.id)
    await hass.async_block_till_done()
    boxes = [e for e in er.async_entries_for_config_entry(reg, setup["entry"].entry_id) if e.domain == "number"]
    assert boxes and all(b.device_id == new_dev.id for b in boxes)


async def test_related_integration_via_entity_attributes(hass, setup, hass_ws_client):
    """Scheduler-style link: another integration's entities list this device's entities."""
    ent = setup["ent"].entity_id
    sched = MockConfigEntry(domain="scheduler", title="Scheduler")
    sched.add_to_hass(hass)
    reg = er.async_get(hass)
    for uid, name, targets in (
        ("a", "Scheduler Office - Weekends", [ent]),
        ("b", "Scheduler Office - Weekdays", [ent, "climate.other"]),
        ("c", "Scheduler Upstairs", ["climate.other"]),
    ):
        e = reg.async_get_or_create("switch", "scheduler", uid, config_entry=sched, suggested_object_id=f"schedule_{uid}")
        hass.states.async_set(e.entity_id, "on", {"entities": targets, "friendly_name": name})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    related = (await client.receive_json())["result"]["components"][0]["related"]
    rows = [r for r in related["entries"] if r["title"] == "Scheduler"]
    assert len(rows) == 1
    assert rows[0]["subtitle"] == "scheduler · Used by 2: Office - Weekdays, Office - Weekends"
    assert rows[0]["link"]["url"].endswith(f"#config_entry={sched.entry_id}")
    # a helper linked through its settings keeps the plain wording
    helper = [r for r in related["helpers"] + related["entries"] if r["title"] == "Kitchen AL"]
    assert helper and helper[0]["subtitle"].endswith("Uses this device")


GROUP_AUTOMATIONS = {
    "name": "Automations (Group)", "title": "Automations (Group)", "style": "list", "column": 1,
    "order": 20, "enabled": True, "empty_text": "No automations trigger on or check a group with this device",
    "source": {"table": "automation_references", "alias": "t0"},
    "joins": [
        {"type": "inner", "table": "group_members", "alias": "t1", "left": "t0.entity_id", "right": "t1.group_entity_id"},
        {"type": "inner", "table": "automations", "alias": "t2", "left": "t0.source_entity_id", "right": "t2.entity_id"},
    ],
    "where": {"match": "all", "conditions": [
        {"column": "t1.member_device_id", "op": "eq", "value_source": "device.id", "value": ""},
        {"column": "t0.part", "op": "in", "value_source": "fixed", "value": "trigger,condition"},
        {"column": "t1.group_platform", "op": "eq", "value_source": "fixed", "value": "group"},
    ]},
    "group_by": ["t2.name", "t2.status", "t2.edit_url"],
    "aggregates": [{"func": "list", "column": "t1.group_name", "label": "Group"}],
    "order_by": [{"column": "t2.name", "direction": "asc"}],
    "limit": 100, "columns": ["t2.name", "agg:0", "t2.status"], "labels": {},
    "row_link": {"type": "url", "column": "t2.edit_url"}, "actions": [],
    "device_filter": {"types": [], "statuses": [], "integrations": [], "areas": []},
}


async def test_automations_referencing_groups_with_this_device(hass, setup):
    """The 'Automations (Group)' component built only from builder tables."""
    ent = setup["ent"].entity_id
    reg = er.async_get(hass)
    grp = reg.async_get_or_create("light", "group", "g1", suggested_object_id="kitchen_lights")
    hass.states.async_set(grp.entity_id, "on", {"entity_id": [ent], "friendly_name": "Kitchen Lights"})
    other = reg.async_get_or_create("light", "linus_dashboard", "g2", suggested_object_id="linus_all")
    hass.states.async_set(other.entity_id, "on", {"entity_id": [ent], "friendly_name": "Linus All"})
    g = grp.entity_id
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "a1", "alias": "Trigger on group", "triggers": [{"trigger": "state", "entity_id": g}], "actions": []},
        {"id": "a2", "alias": "Condition on group", "initial_state": False,
         "triggers": [{"trigger": "event", "event_type": "x"}],
         "conditions": [{"condition": "state", "entity_id": g, "state": "on"}], "actions": []},
        {"id": "a3", "alias": "Action only", "triggers": [{"trigger": "event", "event_type": "y"}],
         "actions": [{"action": "light.turn_on", "target": {"entity_id": g}}]},
        {"id": "a4", "alias": "Non-helper group", "triggers": [{"trigger": "state", "entity_id": other.entity_id}], "actions": []},
    ]})
    await hass.async_block_till_done()
    runtime = setup["entry"].runtime_data
    res = await runtime.async_run_spec(GROUP_AUTOMATIONS, setup["dev"].id)
    rows = [dict(zip(res.columns, r)) for r in res.rows]
    assert [(r["t2.name"], r["agg:0"], r["t2.status"]) for r in rows] == [
        ("Condition on group", "Kitchen Lights", "Inactive"),
        ("Trigger on group", "Kitchen Lights", "Active"),
    ]
    assert rows[1]["t2.edit_url"] == "/config/automation/edit/a1"
    # and the spec is accepted by the engine's validator
    from custom_components.custom_device_page_components import query
    query.validate(GROUP_AUTOMATIONS)


async def test_nested_group_members(hass, setup):
    """A device in a group inside a group is a member of both; loops are cut."""
    ent = setup["ent"].entity_id
    reg = er.async_get(hass)
    inner = reg.async_get_or_create("light", "group", "gi", suggested_object_id="kitchen_lights").entity_id
    outer = reg.async_get_or_create("light", "group", "go", suggested_object_id="all_lights").entity_id
    top = reg.async_get_or_create("light", "group", "gt", suggested_object_id="house").entity_id
    hass.states.async_set(inner, "on", {"entity_id": [ent], "friendly_name": "Kitchen Lights"})
    sched = reg.async_get_or_create("switch", "scheduler", "s1", suggested_object_id="schedule_x").entity_id
    hass.states.async_set(sched, "on", {"entity_id": ["light.elsewhere"], "friendly_name": "Schedule"})
    hass.states.async_set(outer, "on", {"entity_id": [inner, top, sched], "friendly_name": "All Lights"})
    hass.states.async_set(top, "on", {"entity_id": [outer], "friendly_name": "House"})  # loop
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "n1", "alias": "Outer trigger", "triggers": [{"trigger": "state", "entity_id": outer}], "actions": []},
        {"id": "n2", "alias": "Inner and outer", "triggers": [{"trigger": "state", "entity_id": inner}],
         "conditions": [{"condition": "state", "entity_id": top, "state": "on"}], "actions": []},
    ]})
    await hass.async_block_till_done()
    runtime = setup["entry"].runtime_data
    res = await runtime.async_run_spec(GROUP_AUTOMATIONS, setup["dev"].id)
    rows = [dict(zip(res.columns, r)) for r in res.rows]
    got = {r["t2.name"]: sorted(r["agg:0"].split(",")) for r in rows}
    assert got == {"Inner and outer": ["House", "Kitchen Lights"], "Outer trigger": ["All Lights"]}
    # depth and via columns
    spec = {**GROUP_AUTOMATIONS, "source": {"table": "group_members", "alias": "t0"}, "joins": [],
            "where": {"match": "all", "conditions": [
                {"column": "t0.member_entity_id", "op": "eq", "value_source": "fixed", "value": ent}]},
            "group_by": [], "aggregates": [], "row_link": {"type": "none"},
            "columns": ["t0.group_name", "t0.depth", "t0.via_group_name"],
            "order_by": [{"column": "t0.depth", "direction": "asc"}]}
    res = await runtime.async_run_spec(spec, setup["dev"].id)
    assert [tuple(r[:3]) for r in res.rows] == [
        ("Kitchen Lights", 1, "Kitchen Lights"), ("All Lights", 2, "Kitchen Lights"), ("House", 3, "Kitchen Lights")]
    # a non-helper member (Scheduler) is a member, but its targets are not
    spec["where"]["conditions"][0]["value"] = "light.elsewhere"
    res = await runtime.async_run_spec(spec, setup["dev"].id)
    assert [r[0] for r in res.rows] == ["Schedule"]


async def test_confirm_config_changes_option(hass, setup, hass_ws_client):
    """Off by default; the device payload carries it for the frontend."""
    entry = setup["entry"]
    r = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {f["name"]: f.get("default") for f in r["data_schema"].schema and [
        {"name": str(k), "default": k.default() if callable(getattr(k, "default", None)) else None}
        for k in r["data_schema"].schema]}
    assert defaults["confirm_config_changes"] is False
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    assert (await client.receive_json())["result"]["confirm_config_changes"] is False
    with patch("custom_components.custom_device_page_components.add_extra_js_url"):
        r = await hass.config_entries.options.async_configure(r["flow_id"], {**NEW_OPTS, "confirm_config_changes": True})
        await hass.async_block_till_done()
    assert r["type"] == "create_entry"
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    assert (await client.receive_json())["result"]["confirm_config_changes"] is True


MMWAVE_KEYS = [
    ("mmwave_depth_minimum_near", "mmWave Depth Minimum (near)"),
    ("mmwave_depth_maximum_far", "mmWave Depth Maximum (far)"),
    ("mmwave_height_minimum_floor", "mmWave Height Minimum (floor)"),
    ("mmwave_height_maximum_ceiling", "mmWave Height Maximum (ceiling)"),
    ("mmwave_width_minimum_left", "mmWave Width Minimum (left)"),
    ("mmwave_width_maximum_right", "mmWave Width Maximum (right)"),
]
DETECTION_AREA = {
    "name": "Inovelli Detection Area (ft)", "title": "Inovelli Detection Area (ft)",
    "style": "table", "column": 1, "order": 20, "enabled": True, "empty_text": "No mmWave settings",
    "value_list": [{"key": k, "label": v} for k, v in MMWAVE_KEYS],
    "source": {"table": "entities", "alias": "t0"},
    "joins": [
        {"type": "inner", "table": "value_list", "alias": "t1", "left": "t0.setting_key", "right": "t1.key"},
        {"type": "inner", "table": "states", "alias": "t2", "left": "t0.entity_id", "right": "t2.entity_id"},
    ],
    "where": {"match": "all", "conditions": [
        {"column": "t0.device_id", "op": "eq", "value_source": "device.id", "value": ""}]},
    "group_by": [], "aggregates": [],
    "order_by": [{"column": "t1.position", "direction": "asc"}],
    "limit": 100, "columns": ["t1.label", "t2.state"], "labels": {"t1.label": "Setting", "t2.state": "Distance"},
    "formats": {"t2.state": {"convert_from": "cm", "convert_to": "ft", "decimals": 0, "feet_inches": True}},
    "row_link": {"type": "none"}, "actions": [],
    "device_filter": {"types": ["model:Inovelli|VZM32-SN", "model:Inovelli|VZW32-SN"],
                      "statuses": [], "integrations": [], "areas": []},
}


async def test_inovelli_detection_area_in_feet(hass, setup):
    """Your list + setting_key + cm→ft format, across ZHA (translation keys) and Z-Wave (names)."""
    from custom_components.custom_device_page_components import filters, query
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    zha_entry = MockConfigEntry(domain="zha"); zha_entry.add_to_hass(hass)
    zw_entry = MockConfigEntry(domain="zwave_js"); zw_entry.add_to_hass(hass)
    vzm = dev_reg.async_get_or_create(config_entry_id=zha_entry.entry_id, identifiers={("zha", "aa")},
                                      manufacturer="Inovelli", model="VZM32-SN", name="Office")
    vzw = dev_reg.async_get_or_create(config_entry_id=zw_entry.entry_id, identifiers={("zwave_js", "bb")},
                                      manufacturer="Inovelli", model="VZW32-SN", name="Garage")
    zha_vals = {"mmwave_depth_maximum_far": "332", "mmwave_depth_minimum_near": "0",
                "mmwave_height_minimum_floor": "-75", "mmwave_height_maximum_ceiling": "75",
                "mmwave_width_minimum_left": "-55", "mmwave_width_maximum_right": "572",
                "mmwave_stay_life": "300"}
    for key, val in zha_vals.items():
        e = ent_reg.async_get_or_create("number", "zha", f"aa-{key}", device_id=vzm.id,
                                        translation_key=key, original_name=key.replace("_", " "))
        hass.states.async_set(e.entity_id, val)
    zw_names = {"mmWave Depth Maximum (Far)": "600.0", "mmWave Depth Minimum (Near)": "0.0",
                "mmWave Height Minimum (Floor)": "-100.0", "mmWave Height Maximum (Ceiling)": "100.0",
                "mmWave Width Minimum (Left)": "-307.0", "mmWave Width Maximum (Right)": "276.0",
                "mmWave Detection Timeout": "30"}
    for name, val in zw_names.items():
        e = ent_reg.async_get_or_create("number", "zwave_js", f"bb-{name}", device_id=vzw.id, original_name=name)
        hass.states.async_set(e.entity_id, val, {"unit_of_measurement": "cm"})
    query.validate(DETECTION_AREA)
    runtime = setup["entry"].runtime_data
    labels = [v for _, v in MMWAVE_KEYS]
    res = await runtime.async_run_spec(DETECTION_AREA, vzm.id)
    assert res.labels == ["Setting", "Distance"]
    assert [tuple(r) for r in res.rows] == list(zip(labels, ["0′ 0″", "10′ 11″", "-2′ 6″", "2′ 6″", "-1′ 10″", "18′ 9″"]))
    res = await runtime.async_run_spec(DETECTION_AREA, vzw.id)
    assert [tuple(r) for r in res.rows] == list(zip(labels, ["0′ 0″", "19′ 8″", "-3′ 3″", "3′ 3″", "-10′ 1″", "9′ 1″"]))
    # only Inovelli VZM32-SN / VZW32-SN device pages
    flt = DETECTION_AREA["device_filter"]
    assert filters.device_matches(hass, vzm, flt, True) and filters.device_matches(hass, vzw, flt, True)
    assert not filters.device_matches(hass, setup["dev"], flt, True)


async def test_formats_and_value_list_validation():
    from custom_components.custom_device_page_components import query
    bad = {**DETECTION_AREA, "formats": {"t2.state": {"convert_from": "cm", "convert_to": "°C"}}}
    with pytest.raises(query.QueryError):
        query.validate(bad)
    with pytest.raises(query.QueryError):  # format on a column that isn't shown
        query.validate({**DETECTION_AREA, "formats": {"t0.entity_id": {"decimals": 1}}})
    assert query.parse_value_list("a = A\n\n b \nc=") == [
        {"key": "a", "label": "A"}, {"key": "b", "label": "b"}, {"key": "c", "label": ""}]
    assert query.unit_converter({"decimals": 0})("2.6") == "3"
    assert query.unit_converter({"convert_from": "cm", "convert_to": "ft", "decimals": 1})("-1") == "0.0"
    assert query.unit_converter({"decimals": 2})("on") == "on"
    fi = query.unit_converter({"convert_from": "cm", "feet_inches": True, "decimals": 0})
    assert [fi(v) for v in ("600.0", "-600", "0", "30.4", "-1", "332", "unknown")] == [
        "19′ 8″", "-19′ 8″", "0′ 0″", "1′ 0″", "0′ 0″", "10′ 11″", "unknown"]
    assert fi("29.9") == "1′ 0″"  # 11.8 in rounds up to a whole foot
    with pytest.raises(query.QueryError):
        query.unit_converter({"convert_from": "°C", "feet_inches": True})


async def test_builder_steps_for_list_and_formats(hass, setup):
    """The two new builder steps store what the spec above holds."""
    entry = setup["entry"]
    r = await hass.config_entries.subentries.async_init((entry.entry_id, "component"), context={"source": "user"})
    fid = r["flow_id"]
    cfg = hass.config_entries.subentries.async_configure
    await cfg(fid, {"name": "Area", "title": "Area", "style": "table", "column": "1", "order": 20, "enabled": True})
    r = await cfg(fid, {"next_step_id": "value_list"})
    assert r["step_id"] == "value_list"
    await cfg(fid, {"lines": "\n".join(f"{k} = {v}" for k, v in MMWAVE_KEYS)})
    await cfg(fid, {"next_step_id": "source"}); await cfg(fid, {"table": "entities"})
    await cfg(fid, {"next_step_id": "joins"}); await cfg(fid, {"next_step_id": "add_join"})
    await cfg(fid, {"type": "inner", "table": "value_list"})
    r = await cfg(fid, {"left": "t0.setting_key", "right": "t1.key"})
    assert r["step_id"] == "joins"
    await cfg(fid, {"next_step_id": "add_join"}); await cfg(fid, {"type": "inner", "table": "states"})
    await cfg(fid, {"left": "t0.entity_id", "right": "t2.entity_id"}); await cfg(fid, {"next_step_id": "menu"})
    await cfg(fid, {"next_step_id": "columns"})
    await cfg(fid, {"columns": ["t1.label", "t2.state"], "link_type": "none", "link_column": "", "rename": False})
    await cfg(fid, {"next_step_id": "formats"})
    r = await cfg(fid, {"column": "t2.state", "convert_from": "cm", "convert_to": "°C", "decimals": 1})
    assert r["errors"] == {"base": "invalid_format"}
    r = await cfg(fid, {"column": "t2.state", "convert_from": "cm", "convert_to": "", "decimals": 0, "feet_inches": True})
    assert r["step_id"] == "menu"
    await cfg(fid, {"next_step_id": "save"})
    r = await cfg(fid, {})
    assert r["type"] == "create_entry"
    sub = next(s for s in entry.subentries.values() if s.title == "Area")
    assert sub.data["value_list"][0] == {"key": "mmwave_depth_minimum_near", "label": "mmWave Depth Minimum (near)"}
    assert sub.data["formats"] == {"t2.state": {"convert_from": "cm", "convert_to": "", "decimals": 0, "feet_inches": True}}


# --- Inovelli mmWave interference area ------------------------------------

ZHA_IEEE = "0c:2a:6f:ff:fe:d5:7e:a9"


def _mm_devices(hass):
    dev_reg = dr.async_get(hass)
    zha_entry = MockConfigEntry(domain="zha"); zha_entry.add_to_hass(hass)
    zw_entry = MockConfigEntry(domain="zwave_js"); zw_entry.add_to_hass(hass)
    vzm = dev_reg.async_get_or_create(config_entry_id=zha_entry.entry_id, identifiers={("zha", ZHA_IEEE)},
                                      manufacturer="Inovelli", model="VZM32-SN", name="Office Overhead")
    vzw = dev_reg.async_get_or_create(config_entry_id=zw_entry.entry_id, identifiers={("zwave_js", "123-45")},
                                      manufacturer="Inovelli", model="VZW32-SN", name="Entranceway Overhead")
    return vzm, vzw


async def test_mmwave_payloads(hass, setup):
    """Exactly the calls verified live on 2026-11-07."""
    from custom_components.custom_device_page_components import mmwave
    vzm, vzw = _mm_devices(hass)
    for cmd, val in (("generate", 1), ("clear", 3)):
        call = mmwave.build_call(vzm, cmd)
        assert (call.domain, call.service, call.target) == ("zha", "issue_zigbee_cluster_command", None)
        assert call.data == {"ieee": ZHA_IEEE, "endpoint_id": 1, "cluster_id": 64562, "cluster_type": "in",
                             "command": 0, "command_type": "server", "params": {"control_id": val}}
        call = mmwave.build_call(vzw, cmd)
        assert (call.domain, call.service) == ("zwave_js", "set_config_parameter")
        assert call.data == {"parameter": 111, "value": val}
        assert call.target == {"device_id": vzw.id}
    with pytest.raises(ActionRefused):
        mmwave.build_call(vzm, "reset")
    with pytest.raises(ActionRefused):  # not an Inovelli mmWave switch
        mmwave.build_call(setup["dev"], "generate")
    other = dr.async_get(hass).async_get_or_create(
        config_entry_id=setup["entry"].entry_id, identifiers={("zha", "00:00:00:00:00:00:00:01")},
        manufacturer="Inovelli", model="VZM31-SN", name="Dimmer")
    with pytest.raises(ActionRefused):
        mmwave.build_call(other, "generate")
    assert mmwave.is_mmwave_device(vzm) and mmwave.is_mmwave_device(vzw)
    assert not mmwave.is_mmwave_device(other) and not mmwave.is_mmwave_device(setup["dev"])


async def test_mmwave_policy_allow_and_deny(hass, setup):
    """Only control_id / value 1 and 3, only the right shape, model and device."""
    from custom_components.custom_device_page_components import mmwave
    from custom_components.custom_device_page_components.actions import action_level, mmwave_call_allowed
    vzm, vzw = _mm_devices(hass)

    def zha(cid, **over):
        data = {"ieee": ZHA_IEEE, "endpoint_id": 1, "cluster_id": 64562, "cluster_type": "in",
                "command": 0, "command_type": "server", "params": {"control_id": cid}}
        data.update(over)
        return mmwave_call_allowed("zha", "issue_zigbee_cluster_command", data, None, "VZM32-SN", vzm)

    def zw(value, parameter=111, model="VZW32-SN", target=None, extra=None):
        data = {"parameter": parameter, "value": value, **(extra or {})}
        return mmwave_call_allowed("zwave_js", "set_config_parameter", data,
                                   target or {"device_id": vzw.id}, model, vzw)

    assert zha(1) and zha(3) and zw(1) and zw(3)
    for bad in (0, 2, 4, 5, 6, 255, "1", 1.0, None, True):
        assert not zha(bad), bad
        assert not zw(bad), bad
    assert not zha(1, cluster_id=64561)
    assert not zha(1, command=1)  # set_interference_area (manual coordinates)
    assert not zha(1, endpoint_id=2)
    assert not zha(1, command_type="client")
    assert not zha(1, cluster_type="out")
    assert not zha(1, ieee="00:00:00:00:00:00:00:01")  # not this device
    assert not zha(1, params={"control_id": 1, "area_id": 1})
    assert not mmwave_call_allowed("zha", "issue_zigbee_cluster_command",
                                   {"ieee": ZHA_IEEE, "endpoint_id": 1, "cluster_id": 64562, "cluster_type": "in",
                                    "command": 0, "command_type": "server", "params": {"control_id": 1}},
                                   None, "VZW32-SN", vzm)  # wrong model
    assert not zw(1, parameter=110)
    assert not zw(1, model="VZM32-SN")
    assert not zw(1, target={"device_id": "someone-else"})
    assert not zw(1, target={"entity_id": "light.x"})
    assert not zw(1, extra={"endpoint": 0})
    assert not mmwave_call_allowed("zwave_js", "bulk_set_partial_config_parameters",
                                   {"parameter": 111, "value": 1}, {"device_id": vzw.id}, "VZW32-SN", vzw)
    # the general row-action policy is unchanged
    assert action_level("zwave_js.set_config_parameter") == "blocked"
    assert action_level("zha.issue_zigbee_cluster_command") == "blocked"
    assert action_level("light.clear_something") == "blocked"


async def test_mmwave_websocket_and_page(hass, setup, hass_ws_client, hass_admin_user):
    """Card only on VZM32-SN/VZW32-SN pages; confirmed press calls the action as the presser."""
    from homeassistant.core import ServiceCall
    vzm, vzw = _mm_devices(hass)
    calls: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("zha", "issue_zigbee_cluster_command", record)
    hass.services.async_register("zwave_js", "set_config_parameter", record)
    client = await hass_ws_client(hass)

    async def ws(**msg):
        await client.send_json_auto_id(msg)
        return await client.receive_json()

    page = await ws(type=f"{DOMAIN}/device", device_id=vzm.id)
    mm = [c for c in page["result"]["components"] if c["type"] == "mmwave"]
    assert len(mm) == 1 and mm[0]["column"] == 3
    assert [b["label"] for b in mm[0]["buttons"]] == ["Generate interference area", "Clear interference area"]
    assert "20 seconds" in mm[0]["buttons"][0]["confirm_text"] and "orange" in mm[0]["buttons"][0]["confirm_text"]
    assert "Clear interference area" in mm[0]["buttons"][0]["confirm_text"]
    page = await ws(type=f"{DOMAIN}/device", device_id=setup["dev"].id)
    assert not [c for c in page["result"]["components"] if c["type"] == "mmwave"]

    r = await ws(type=f"{DOMAIN}/mmwave_command", device_id=vzm.id, command="generate", confirmed=False)
    assert not r["success"] and not calls
    r = await ws(type=f"{DOMAIN}/mmwave_command", device_id=setup["dev"].id, command="generate", confirmed=True)
    assert not r["success"] and not calls
    r = await ws(type=f"{DOMAIN}/mmwave_command", device_id=vzm.id, command="reset", confirmed=True)
    assert not r["success"] and not calls

    r = await ws(type=f"{DOMAIN}/mmwave_command", device_id=vzm.id, command="generate", confirmed=True)
    assert r["success"], r
    assert calls[-1].domain == "zha" and calls[-1].data["params"] == {"control_id": 1}
    assert calls[-1].data["ieee"] == ZHA_IEEE
    assert calls[-1].context.user_id == hass_admin_user.id
    r = await ws(type=f"{DOMAIN}/mmwave_command", device_id=vzw.id, command="clear", confirmed=True)
    assert r["success"], r
    assert calls[-1].domain == "zwave_js" and calls[-1].data["parameter"] == 111 and calls[-1].data["value"] == 3
    assert calls[-1].data["device_id"] == vzw.id  # target merged into the call by HA
    assert len(calls) == 2
