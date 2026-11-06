"""Integration tests against a real Home Assistant core."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

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
