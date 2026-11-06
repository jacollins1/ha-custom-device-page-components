"""Declarative query engine.

Users never write SQL. A component stores a *spec* (plain JSON built by the
config-flow UI). The spec is validated against a fixed schema of read-only
tables, compiled to a parameterised SELECT with whitelisted identifiers, and
run against a private in-memory SQLite database built from a snapshot of
Home Assistant's registries and states. Home Assistant's own databases are
never opened, and the in-memory database is switched to query-only before
the user's query runs.

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# Schema: table -> ordered columns. Labels are for the UI.
# ---------------------------------------------------------------------------

SCHEMA: dict[str, dict[str, Any]] = {
    "devices": {
        "label": "Devices",
        "columns": [
            "id", "display_name", "name", "name_by_user", "manufacturer",
            "model", "model_id", "sw_version", "hw_version", "serial_number",
            "area_id", "entry_type", "disabled_by", "via_device_id",
            "primary_config_entry", "integration", "configuration_url",
            "labels", "available",
        ],
    },
    "device_config_entries": {
        "label": "Device ↔ integration entries",
        "columns": ["device_id", "config_entry_id", "domain"],
    },
    "entities": {
        "label": "Entities",
        "columns": [
            "entity_id", "registry_id", "unique_id", "platform", "domain",
            "device_id", "area_id", "effective_area_id", "config_entry_id",
            "display_name", "name", "original_name", "disabled_by",
            "hidden_by", "entity_category", "device_class",
            "unit_of_measurement", "icon", "labels",
        ],
    },
    "states": {
        "label": "Current states",
        "columns": [
            "entity_id", "state", "last_changed", "last_updated",
            "friendly_name", "unit_of_measurement", "device_class",
            "attributes",
        ],
    },
    "areas": {
        "label": "Areas",
        "columns": ["area_id", "name", "floor_id", "icon", "aliases"],
    },
    "floors": {"label": "Floors", "columns": ["floor_id", "name", "level"]},
    "labels": {
        "label": "Labels",
        "columns": ["label_id", "name", "color", "description"],
    },
    "device_labels": {
        "label": "Device ↔ labels", "columns": ["device_id", "label_id"],
    },
    "entity_labels": {
        "label": "Entity ↔ labels", "columns": ["entity_id", "label_id"],
    },
    "config_entries": {
        "label": "Integration entries",
        "columns": [
            "entry_id", "domain", "title", "state", "source", "disabled_by",
            "integration_name", "integration_type",
        ],
    },
    "dashboards": {
        "label": "Dashboards",
        "columns": ["url_path", "title", "mode", "require_admin",
                    "show_in_sidebar"],
    },
    "dashboard_references": {
        "label": "Dashboard references",
        "columns": ["url_path", "view_index", "view_path", "view_title",
                    "device_id", "entity_id"],
    },
    "automation_references": {
        "label": "Automation & script references",
        # part: trigger / condition (top-level automation conditions) /
        # action (actions, incl. conditions inside them; all of a script).
        "columns": ["source_entity_id", "source_domain", "device_id",
                    "entity_id", "part"],
    },
    "automations": {
        "label": "Automations",
        "columns": ["entity_id", "name", "state", "status", "config_id",
                    "edit_url", "mode", "last_triggered"],
    },
    "group_members": {
        "label": "Group members",
        # Every entity whose state lists members in its entity_id attribute:
        # group helpers (platform "group"), YAML groups, and groups made by
        # other integrations (platform = that integration). Members of groups
        # inside a group count too: depth 1 = listed directly, 2 = member of a
        # group listed in it, and so on; via_group_* is the group that lists
        # the member directly.
        "columns": ["group_entity_id", "group_name", "group_domain",
                    "group_platform", "member_entity_id", "member_device_id",
                    "depth", "via_group_entity_id", "via_group_name"],
    },
    "relations": {
        "label": "Related items (built-in index)",
        "columns": ["device_id", "category", "title", "subtitle",
                    "link_type", "link_target"],
    },
}

# Fields of the device whose page is open, usable as WHERE values.
DEVICE_CONTEXT_FIELDS = [
    "id", "display_name", "area_id", "manufacturer", "model",
    "primary_config_entry", "integration", "via_device_id",
]

JOIN_TYPES = {
    "inner": "INNER JOIN",
    "left": "LEFT OUTER JOIN",
    "right": "RIGHT OUTER JOIN",
    "full": "FULL OUTER JOIN",
}

OPERATORS = [
    "eq", "ne", "lt", "le", "gt", "ge", "contains", "not_contains",
    "starts_with", "ends_with", "in", "not_in", "is_empty", "is_not_empty",
]
NO_VALUE_OPERATORS = {"is_empty", "is_not_empty"}

AGGREGATES = {
    "count": "COUNT", "count_distinct": "COUNT", "min": "MIN", "max": "MAX",
    "sum": "SUM", "avg": "AVG", "list": "GROUP_CONCAT",
}

LINK_TYPES = ["none", "entity", "device", "area", "config_entry", "url"]

MAX_LIMIT = 500
# Displayed columns per component (aggregates included; hidden link/action
# key columns are not counted).
MAX_COLUMNS = 8
DEFAULT_LIMIT = 100
# SQLite VM instructions before a query is aborted (protects the event loop
# executor from runaway joins).
MAX_VM_STEPS = 20_000_000


class QueryError(ValueError):
    """Raised for an invalid spec; message is shown to the user."""


@dataclass
class QueryResult:
    """Rows plus column metadata."""

    columns: list[str]
    labels: list[str]
    rows: list[list[Any]]
    truncated: bool = False


# ---------------------------------------------------------------------------
# Helpers for the UI
# ---------------------------------------------------------------------------


def aliases_in(spec: dict[str, Any]) -> dict[str, str]:
    """Return alias -> table for the source and every join."""
    out: dict[str, str] = {}
    src = spec.get("source") or {}
    if src.get("table"):
        out[src.get("alias") or "t0"] = src["table"]
    for join in spec.get("joins") or []:
        out[join["alias"]] = join["table"]
    return out


def available_columns(spec: dict[str, Any]) -> list[str]:
    """All qualified columns (alias.column) the spec can reference."""
    cols: list[str] = []
    for alias, table in aliases_in(spec).items():
        cols.extend(f"{alias}.{c}" for c in SCHEMA[table]["columns"])
    return cols


def column_label(spec: dict[str, Any], ref: str) -> str:
    """Human label for a column or aggregate reference."""
    if ref.startswith("agg:"):
        aggs = spec.get("aggregates") or []
        idx = int(ref[4:])
        if 0 <= idx < len(aggs):
            agg = aggs[idx]
            return agg.get("label") or f"{agg['func']}({agg['column']})"
        return ref
    labels = spec.get("labels") or {}
    if ref in labels:
        return labels[ref]
    alias, _, col = ref.partition(".")
    table = aliases_in(spec).get(alias, alias)
    return f"{SCHEMA.get(table, {}).get('label', table)}: {col}"


def next_alias(spec: dict[str, Any]) -> str:
    """Return an unused alias t<n>."""
    used = set(aliases_in(spec))
    n = 0
    while f"t{n}" in used:
        n += 1
    return f"t{n}"


# ---------------------------------------------------------------------------
# Validation and compilation
# ---------------------------------------------------------------------------


def _col(spec_aliases: dict[str, str], ref: str) -> str:
    """Validate alias.column and return a quoted SQL identifier pair."""
    if not isinstance(ref, str) or "." not in ref:
        raise QueryError(f"Unknown column: {ref!r}")
    alias, col = ref.split(".", 1)
    table = spec_aliases.get(alias)
    if table is None:
        raise QueryError(f"Column {ref!r} uses a table that is not in the query")
    if col not in SCHEMA[table]["columns"]:
        raise QueryError(f"Table {table!r} has no column {col!r}")
    return f'"{alias}"."{col}"'


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def validate(spec: dict[str, Any]) -> None:
    """Raise QueryError if the spec cannot be compiled."""
    compile_spec(spec, {k: None for k in DEVICE_CONTEXT_FIELDS})


def compile_spec(
    spec: dict[str, Any], device: dict[str, Any]
) -> tuple[str, list[Any], list[str]]:
    """Compile a spec to (sql, params, output column refs)."""
    src = spec.get("source") or {}
    table = src.get("table")
    if table not in SCHEMA:
        raise QueryError("Choose a source table")
    aliases: dict[str, str] = {src.get("alias") or "t0": table}
    params: list[Any] = []

    from_sql = f'"{table}" AS "{src.get("alias") or "t0"}"'
    for join in spec.get("joins") or []:
        jtype = JOIN_TYPES.get(join.get("type"))
        if jtype is None:
            raise QueryError(f"Unknown join type {join.get('type')!r}")
        jtable = join.get("table")
        if jtable not in SCHEMA:
            raise QueryError(f"Unknown table {jtable!r}")
        jalias = join.get("alias")
        if not jalias or jalias in aliases or not jalias.isidentifier():
            raise QueryError(f"Invalid or duplicate alias {jalias!r}")
        left = _col(aliases, join.get("left"))
        aliases[jalias] = jtable
        right = _col(aliases, join.get("right"))
        if not join.get("right", "").startswith(f"{jalias}."):
            raise QueryError("The ON column must belong to the joined table")
        from_sql += f' {jtype} "{jtable}" AS "{jalias}" ON {left} = {right}'

    # WHERE
    where = spec.get("where") or {}
    conds_sql: list[str] = []
    for cond in where.get("conditions") or []:
        col = _col(aliases, cond.get("column"))
        op = cond.get("op")
        if op not in OPERATORS:
            raise QueryError(f"Unknown operator {op!r}")
        if op in NO_VALUE_OPERATORS:
            sql = f"({col} IS NULL OR {col} = '')"
            conds_sql.append(sql if op == "is_empty" else f"NOT {sql}")
            continue
        source = cond.get("value_source") or "fixed"
        if source == "fixed":
            value = cond.get("value", "")
        elif source.startswith("device."):
            field = source[7:]
            if field not in DEVICE_CONTEXT_FIELDS:
                raise QueryError(f"Unknown device field {field!r}")
            value = device.get(field)
        else:
            raise QueryError(f"Unknown value source {source!r}")
        if op in ("in", "not_in"):
            items = [v.strip() for v in str(value or "").split(",") if v.strip()]
            if not items:
                conds_sql.append("0" if op == "in" else "1")
                continue
            marks = ",".join("?" * len(items))
            params.extend(items)
            neg = "NOT " if op == "not_in" else ""
            conds_sql.append(f"{col} {neg}IN ({marks})")
            continue
        if op in ("contains", "not_contains", "starts_with", "ends_with"):
            esc = (
                str(value or "")
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = {
                "contains": f"%{esc}%",
                "not_contains": f"%{esc}%",
                "starts_with": f"{esc}%",
                "ends_with": f"%{esc}",
            }[op]
            params.append(pattern)
            neg = "NOT " if op == "not_contains" else ""
            conds_sql.append(f"{col} {neg}LIKE ? ESCAPE '\\'")
            continue
        sym = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">",
               "ge": ">="}[op]
        if op in ("lt", "le", "gt", "ge") and _is_number(value):
            params.append(float(value))
            conds_sql.append(f"CAST({col} AS REAL) {sym} ?")
        elif value is None:
            conds_sql.append(f"{col} IS {'NOT ' if op == 'ne' else ''}NULL")
        else:
            params.append(value)
            if op == "ne":
                conds_sql.append(f"({col} IS NULL OR {col} != ?)")
            else:
                conds_sql.append(f"{col} {sym} ?")
    joiner = " OR " if where.get("match") == "any" else " AND "

    # GROUP BY and aggregates
    group_by = [_col(aliases, c) for c in spec.get("group_by") or []]
    agg_sql: list[str] = []
    for agg in spec.get("aggregates") or []:
        func = agg.get("func")
        if func not in AGGREGATES:
            raise QueryError(f"Unknown aggregate {func!r}")
        col = "*" if agg.get("column") == "*" and func == "count" else _col(
            aliases, agg.get("column")
        )
        if func == "count_distinct":
            agg_sql.append(f"COUNT(DISTINCT {col})")
        elif func == "list":
            agg_sql.append(f"GROUP_CONCAT(DISTINCT {col})")
        else:
            agg_sql.append(f"{AGGREGATES[func]}({col})")

    # Output columns
    columns = list(spec.get("columns") or [])
    if len(columns) > MAX_COLUMNS:
        raise QueryError(
            f"Show at most {MAX_COLUMNS} columns ({len(columns)} chosen): "
            "edit the component's Columns step"
        )
    if not columns:
        columns = [f"agg:{i}" for i in range(len(agg_sql))] or [
            f"{alias}.{c}"
            for alias, t in list(aliases.items())[:1]
            for c in SCHEMA[t]["columns"][:4]
        ]
    grouped = bool(group_by or agg_sql)
    group_refs = set(spec.get("group_by") or [])
    select_sql: list[str] = []
    for ref in columns:
        if ref.startswith("agg:"):
            idx = int(ref[4:]) if ref[4:].isdigit() else -1
            if not 0 <= idx < len(agg_sql):
                raise QueryError(f"Unknown aggregate column {ref!r}")
            select_sql.append(agg_sql[idx])
        else:
            if grouped and ref not in group_refs:
                raise QueryError(
                    f"{ref} is shown but not grouped: add it to GROUP BY or "
                    "remove it from the displayed columns"
                )
            select_sql.append(_col(aliases, ref))
    # Hidden key columns needed for links/actions are appended.
    extra: list[str] = []
    for ref in _extra_refs(spec):
        if ref not in columns and ref not in extra:
            if grouped and ref not in group_refs:
                raise QueryError(
                    f"{ref} is used by a link or action but is not grouped"
                )
            extra.append(ref)
            select_sql.append(_col(aliases, ref))

    sql = f"SELECT {', '.join(select_sql)} FROM {from_sql}"
    if conds_sql:
        sql += " WHERE " + joiner.join(f"({c})" for c in conds_sql)
    if group_by:
        sql += " GROUP BY " + ", ".join(group_by)

    order_sql: list[str] = []
    for order in spec.get("order_by") or []:
        ref = order.get("column")
        direction = "DESC" if order.get("direction") == "desc" else "ASC"
        if ref.startswith("agg:"):
            idx = int(ref[4:]) if ref[4:].isdigit() else -1
            if not 0 <= idx < len(agg_sql):
                raise QueryError(f"Unknown aggregate column {ref!r}")
            order_sql.append(f"{agg_sql[idx]} {direction}")
        else:
            order_sql.append(f"{_col(aliases, ref)} {direction}")
    if order_sql:
        sql += " ORDER BY " + ", ".join(order_sql)

    limit = spec.get("limit")
    limit = DEFAULT_LIMIT if limit in (None, "", 0) else int(limit)
    if not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"LIMIT must be between 1 and {MAX_LIMIT}")
    # One extra row tells us the result was truncated.
    sql += f" LIMIT {limit + 1}"
    return sql, params, columns + extra


def _extra_refs(spec: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    link = spec.get("row_link") or {}
    if link.get("type") not in (None, "none") and link.get("column"):
        refs.append(link["column"])
    for action in spec.get("actions") or []:
        if action.get("target_column"):
            refs.append(action["target_column"])
    return refs


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def sqlite_supports_outer_joins() -> bool:
    """RIGHT and FULL OUTER JOIN need SQLite 3.39."""
    return sqlite3.sqlite_version_info >= (3, 39, 0)


def run(
    spec: dict[str, Any],
    tables: dict[str, list[tuple]],
    device: dict[str, Any],
) -> QueryResult:
    """Compile and run a spec. Blocking: call from an executor."""
    for join in spec.get("joins") or []:
        if join.get("type") in ("right", "full") and not (
            sqlite_supports_outer_joins()
        ):
            raise QueryError(
                "RIGHT/FULL OUTER JOIN needs SQLite 3.39 or newer; this system "
                f"has {sqlite3.sqlite_version}"
            )
    sql, params, refs = compile_spec(spec, device)
    used = set(aliases_in(spec).values())
    conn = sqlite3.connect(":memory:")
    try:
        for name in used:
            cols = SCHEMA[name]["columns"]
            col_sql = ", ".join('"' + c + '"' for c in cols)
            conn.execute(f'CREATE TABLE "{name}" ({col_sql})')
            rows = tables.get(name) or []
            if rows:
                conn.executemany(
                    f'INSERT INTO "{name}" VALUES ({",".join("?" * len(cols))})',
                    rows,
                )
        conn.execute("PRAGMA query_only = ON")
        steps = {"n": 0}

        def _guard() -> int:
            steps["n"] += 1
            return 1 if steps["n"] * 1000 > MAX_VM_STEPS else 0

        conn.set_progress_handler(_guard, 1000)
        try:
            cur = conn.execute(sql, params)
            rows = [list(r) for r in cur.fetchall()]
        except sqlite3.OperationalError as err:
            if "interrupted" in str(err):
                raise QueryError(
                    "The query was stopped because it took too long; "
                    "add conditions or a join condition"
                ) from err
            raise QueryError(str(err)) from err
    finally:
        conn.close()
    limit = spec.get("limit") or DEFAULT_LIMIT
    truncated = len(rows) > int(limit)
    rows = rows[: int(limit)]
    return QueryResult(
        columns=refs,
        labels=[column_label(spec, r) for r in refs],
        rows=rows,
        truncated=truncated,
    )
