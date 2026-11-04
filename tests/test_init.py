"""Integration tests against a real Home Assistant core."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.custom_device_page_components.const import DOMAIN

OPTS = {
    "related_enabled": True, "related_order": 0, "color_temp_enabled": True,
    "color_temp_unit": "mireds", "color_temp_models": [], "bindings_enabled": False,
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


async def test_color_temp_box_on_device(hass, setup):
    reg = er.async_get(hass)
    num = [e for e in er.async_entries_for_device(reg, setup["dev"].id) if e.domain == "number"]
    assert len(num) == 1 and num[0].config_entry_id == setup["entry"].entry_id
    state = hass.states.get(num[0].entity_id)
    assert state.state == "370"  # floor(1e6 / 2702)
    assert state.attributes["min"] == 153 and state.attributes["max"] == 500
    # ZHA stays the only integration on the device
    assert dr.async_get(hass).async_get(setup["dev"].id).config_entry_id == setup["dev"].config_entry_id

    calls = []
    hass.services.async_register("light", "turn_on", lambda c: calls.append(c))
    await hass.services.async_call("number", "set_value", {"entity_id": num[0].entity_id, "value": 370}, blocking=True)
    assert calls[0].data["color_temp_kelvin"] == 2702


async def test_device_payload(hass, setup, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/device", "device_id": setup["dev"].id})
    res = (await client.receive_json())["result"]
    ids = [c["id"] for c in res["components"]]
    assert ids[0] == "related"
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
    r = await mgr.async_configure(r["flow_id"], {"name": "Areas", "style": "list", "order": 3, "enabled": True})
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


async def test_options_warn_before_delete(hass, setup):
    entry = setup["entry"]
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {**OPTS, "color_temp_enabled": False})
    assert r["step_id"] == "confirm_remove"
    assert r["description_placeholders"]["count"] == "1"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"confirm": False})
    assert r["errors"] == {"base": "confirm_required"}
    with patch("custom_components.custom_device_page_components.add_extra_js_url"):
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"confirm": True})
        await hass.async_block_till_done()
    assert r["type"] == "create_entry"
    reg = er.async_get(hass)
    assert not [e for e in er.async_entries_for_device(reg, setup["dev"].id) if e.domain == "number"]


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
