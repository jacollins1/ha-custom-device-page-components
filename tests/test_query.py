"""Query engine (no Home Assistant needed)."""

import pytest

from custom_components.custom_device_page_components import query

DEV = {k: None for k in query.DEVICE_CONTEXT_FIELDS} | {"id": "dev1", "area_id": "kitchen"}

E = query.SCHEMA["entities"]["columns"]
D = query.SCHEMA["devices"]["columns"]
S = query.SCHEMA["states"]["columns"]


def ent(entity_id, device_id, domain=None, area=None):
    row = dict.fromkeys(E)
    row.update(entity_id=entity_id, device_id=device_id, domain=domain or entity_id.split(".")[0], area_id=area)
    return tuple(row[c] for c in E)


def dev(id_, name, area):
    row = dict.fromkeys(D)
    row.update(id=id_, display_name=name, area_id=area)
    return tuple(row[c] for c in D)


def st(entity_id, state):
    row = dict.fromkeys(S)
    row.update(entity_id=entity_id, state=state)
    return tuple(row[c] for c in S)


TABLES = {
    "entities": [
        ent("light.a", "dev1"), ent("sensor.t", "dev1"), ent("light.b", "dev2"),
        ent("switch.orphan", None),
    ],
    "devices": [dev("dev1", "Kitchen lamp", "kitchen"), dev("dev2", "Hall lamp", "hall"), dev("dev3", "Spare", None)],
    "states": [st("light.a", "on"), st("sensor.t", "21.5"), st("light.b", "off"), st("sensor.t2", "9")],
}


def spec(**kw):
    base = {"source": {"table": "entities", "alias": "t0"}, "joins": [], "where": {"match": "all", "conditions": []},
            "group_by": [], "aggregates": [], "order_by": [], "limit": 100, "columns": [], "labels": {},
            "row_link": {"type": "none"}, "actions": []}
    base.update(kw)
    return base


def test_where_this_device():
    s = spec(columns=["t0.entity_id"], where={"match": "all", "conditions": [
        {"column": "t0.device_id", "op": "eq", "value_source": "device.id"}]},
        order_by=[{"column": "t0.entity_id", "direction": "asc"}])
    r = query.run(s, TABLES, DEV)
    assert r.rows == [["light.a"], ["sensor.t"]]


def test_inner_left_right_full_joins():
    base = dict(joins=[{"type": "inner", "table": "devices", "alias": "t1", "left": "t0.device_id", "right": "t1.id"}],
                columns=["t0.entity_id", "t1.display_name"], order_by=[{"column": "t0.entity_id", "direction": "asc"}])
    assert len(query.run(spec(**base), TABLES, DEV).rows) == 3
    base["joins"][0]["type"] = "left"
    assert len(query.run(spec(**base), TABLES, DEV).rows) == 4
    base["joins"][0]["type"] = "right"
    assert len(query.run(spec(**base), TABLES, DEV).rows) == 4  # dev3 has no entities
    base["joins"][0]["type"] = "full"
    assert len(query.run(spec(**base), TABLES, DEV).rows) == 5


def test_group_by_order_limit():
    s = spec(group_by=["t0.domain"], aggregates=[{"func": "count", "column": "*", "label": "N"}],
             columns=["t0.domain", "agg:0"], order_by=[{"column": "agg:0", "direction": "desc"},
                                                      {"column": "t0.domain", "direction": "asc"}], limit=1)
    r = query.run(s, TABLES, DEV)
    assert r.rows == [["light", 2]]
    assert r.truncated is True
    assert r.labels == ["Entities: domain", "N"]


def test_numeric_compare_and_like():
    s = spec(source={"table": "states", "alias": "t0"}, columns=["t0.entity_id"],
             where={"match": "all", "conditions": [
                 {"column": "t0.state", "op": "gt", "value_source": "fixed", "value": "10"},
                 {"column": "t0.entity_id", "op": "starts_with", "value_source": "fixed", "value": "sensor."}]})
    assert query.run(s, TABLES, DEV).rows == [["sensor.t"]]


def test_or_and_in_and_empty():
    s = spec(columns=["t0.entity_id"], order_by=[{"column": "t0.entity_id", "direction": "asc"}],
             where={"match": "any", "conditions": [
                 {"column": "t0.domain", "op": "in", "value_source": "fixed", "value": "sensor, switch"},
                 {"column": "t0.device_id", "op": "is_empty"}]})
    assert query.run(s, TABLES, DEV).rows == [["sensor.t"], ["switch.orphan"]]


def test_like_escapes_wildcards():
    s = spec(columns=["t0.entity_id"], where={"match": "all", "conditions": [
        {"column": "t0.entity_id", "op": "contains", "value_source": "fixed", "value": "%"}]})
    assert query.run(s, TABLES, DEV).rows == []


@pytest.mark.parametrize("bad", [
    {"source": {"table": "sqlite_master", "alias": "t0"}},
    {"columns": ['t0.entity_id"; DROP TABLE x; --']},
    {"columns": ["t9.entity_id"]},
    {"where": {"conditions": [{"column": "t0.entity_id", "op": "regexp", "value": "x"}]}},
    {"group_by": ["t0.domain"], "columns": ["t0.entity_id"]},
    {"limit": 5000},
    {"joins": [{"type": "cross", "table": "devices", "alias": "t1", "left": "t0.device_id", "right": "t1.id"}]},
    {"joins": [{"type": "left", "table": "devices", "alias": "t1", "left": "t0.device_id", "right": "t0.device_id"}]},
])
def test_rejects_invalid(bad):
    with pytest.raises(query.QueryError):
        query.validate(spec(**bad))


def test_values_are_parameters_not_sql():
    s = spec(columns=["t0.entity_id"], where={"match": "all", "conditions": [
        {"column": "t0.entity_id", "op": "eq", "value_source": "fixed", "value": "x' OR '1'='1"}]})
    assert query.run(s, TABLES, DEV).rows == []


def test_runaway_query_is_stopped(monkeypatch):
    monkeypatch.setattr(query, "MAX_VM_STEPS", 5000)
    big = {"entities": [ent(f"light.l{i}", f"d{i % 7}") for i in range(400)]}
    s = spec(joins=[{"type": "inner", "table": "entities", "alias": "t1", "left": "t0.domain", "right": "t1.domain"},
                    {"type": "inner", "table": "entities", "alias": "t2", "left": "t1.domain", "right": "t2.domain"}],
             aggregates=[{"func": "count", "column": "*"}], columns=["agg:0"])
    with pytest.raises(query.QueryError, match="too long"):
        query.run(s, big, DEV)
