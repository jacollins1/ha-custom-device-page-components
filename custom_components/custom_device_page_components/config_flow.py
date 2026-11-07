"""Config flow, options flow and the component builder (config subentries)."""

from __future__ import annotations

import copy
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlowWithReload,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    BooleanSelector,
    DeviceSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from . import query
from .actions import action_level, available_actions
from .const import (
    COLUMNS,
    CONF_BINDINGS,
    CONF_CONFIRM_CONFIG,
    CONF_COLOR_TEMP_KELVIN,
    CONF_COLOR_TEMP_MIREDS,
    CONF_COLOR_TEMP_MODELS,
    CONF_RELATED,
    CONF_RELATED_COLUMN,
    CONF_RELATED_ORDER,
    DEFAULT_OPTIONS,
    DOMAIN,
    KIND_CT_KELVIN,
    KIND_CT_MIREDS,
    KINDS_BINDINGS,
    NAME,
    SUBENTRY_COMPONENT,
)
from .filters import STATUS_VALUES, integration_options, type_options


def _select(
    options: list[SelectOptionDict] | list[str],
    multiple: bool = False,
    custom: bool = False,
    dropdown: bool = True,
) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=multiple,
            custom_value=custom,
            mode=SelectSelectorMode.DROPDOWN if dropdown else SelectSelectorMode.LIST,
            sort=False,
        )
    )


def _builtin_schema(hass, defaults: dict[str, Any]) -> vol.Schema:
    models = _ct_models(hass)
    return vol.Schema(
        {
            vol.Required(CONF_RELATED, default=defaults[CONF_RELATED]): BooleanSelector(),
            vol.Required(
                CONF_RELATED_ORDER, default=defaults[CONF_RELATED_ORDER]
            ): NumberSelector(
                NumberSelectorConfig(min=-100, max=100, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_RELATED_COLUMN, default=str(defaults[CONF_RELATED_COLUMN])
            ): _column_select(),
            vol.Required(
                CONF_COLOR_TEMP_KELVIN, default=defaults[CONF_COLOR_TEMP_KELVIN]
            ): BooleanSelector(),
            vol.Required(
                CONF_COLOR_TEMP_MIREDS, default=defaults[CONF_COLOR_TEMP_MIREDS]
            ): BooleanSelector(),
            vol.Optional(
                CONF_COLOR_TEMP_MODELS, default=defaults[CONF_COLOR_TEMP_MODELS]
            ): _select(models, multiple=True, custom=True),
            vol.Required(CONF_BINDINGS, default=defaults[CONF_BINDINGS]): BooleanSelector(),
            vol.Required(
                CONF_CONFIRM_CONFIG, default=defaults[CONF_CONFIRM_CONFIG]
            ): BooleanSelector(),
        }
    )


def _column_select() -> SelectSelector:
    """Device page column: 1 (left), 2 (middle) or 3 (right)."""
    return _select(
        [
            SelectOptionDict(value="1", label="1 (left, under Device info)"),
            SelectOptionDict(value="2", label="2 (middle, under Configuration)"),
            SelectOptionDict(value="3", label="3 (right, under Activity)"),
        ],
        dropdown=False,
    )


def _column(value: Any) -> int:
    """A stored column number (1-3); anything else is column 1."""
    try:
        col = int(value)
    except (TypeError, ValueError):
        return 1
    return col if col in COLUMNS else 1


def _ct_models(hass) -> list[str]:
    """Models of devices that have a color-temperature light."""
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    dev_reg = dr.async_get(hass)
    models: set[str] = set()
    for entry in er.async_get(hass).entities.values():
        if entry.domain != "light" or not entry.device_id:
            continue
        modes = (entry.capabilities or {}).get("supported_color_modes") or []
        if "color_temp" not in modes:
            continue
        if (dev := dev_reg.async_get(entry.device_id)) and dev.model:
            models.add(dev.model)
    return sorted(models)


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    out = dict(user_input)
    out[CONF_RELATED_ORDER] = int(out.get(CONF_RELATED_ORDER, 0))
    out[CONF_RELATED_COLUMN] = _column(out.get(CONF_RELATED_COLUMN, 1))
    return out


class CDPCConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the integration (one entry per Home Assistant)."""

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the built-in components."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options=_clean(user_input))
        return self.async_show_form(
            step_id="user",
            data_schema=_builtin_schema(self.hass, dict(DEFAULT_OPTIONS)),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CDPCOptionsFlow:
        """Options flow."""
        return CDPCOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Custom components are subentries."""
        return {SUBENTRY_COMPONENT: ComponentFlow}


class CDPCOptionsFlow(OptionsFlowWithReload):
    """Turn built-in components on or off, with a warning before removals."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Built-in settings."""
        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        if user_input is not None:
            new = _clean(user_input)
            removed = self._removed_entities(current, new)
            if removed:
                self._pending = new
                return await self.async_step_confirm_remove()
            return self.async_create_entry(data=new)
        return self.async_show_form(
            step_id="init", data_schema=_builtin_schema(self.hass, current)
        )

    def _removed_entities(self, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
        ent_reg = er.async_get(self.hass)
        mine = er.async_entries_for_config_entry(ent_reg, self.config_entry.entry_id)
        out = []
        for e in mine:
            kind = (e.unique_id or "").rsplit("_", 1)[-1]
            for key, kinds in (
                (CONF_COLOR_TEMP_MIREDS, (KIND_CT_MIREDS,)),
                (CONF_COLOR_TEMP_KELVIN, (KIND_CT_KELVIN,)),
                (CONF_BINDINGS, KINDS_BINDINGS),
            ):
                if old[key] and not new[key] and kind in kinds:
                    out.append(e.entity_id)
        return out

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Warn before entities are deleted."""
        assert self._pending is not None
        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        removed = self._removed_entities(current, self._pending)
        if user_input is not None:
            if not user_input.get("confirm"):
                return self.async_show_form(
                    step_id="confirm_remove",
                    data_schema=vol.Schema({vol.Required("confirm", default=False): BooleanSelector()}),
                    errors={"base": "confirm_required"},
                    description_placeholders=_removal_placeholders(removed),
                )
            return self.async_create_entry(data=self._pending)
        return self.async_show_form(
            step_id="confirm_remove",
            data_schema=vol.Schema({vol.Required("confirm", default=False): BooleanSelector()}),
            description_placeholders=_removal_placeholders(removed),
        )


def _removal_placeholders(removed: list[str]) -> dict[str, str]:
    shown = removed[:15]
    more = f"\n… and {len(removed) - 15} more" if len(removed) > 15 else ""
    return {
        "count": str(len(removed)),
        "entities": "\n".join(f"- `{e}`" for e in shown) + more,
    }


# ---------------------------------------------------------------------------
# Component builder
# ---------------------------------------------------------------------------

EMPTY_SPEC: dict[str, Any] = {
    "name": "",
    "title": "",
    "style": "table",
    "column": 1,
    "order": 10,
    "enabled": True,
    "empty_text": "",
    "source": {},
    "joins": [],
    "where": {"match": "all", "conditions": []},
    "group_by": [],
    "aggregates": [],
    "order_by": [],
    "limit": query.DEFAULT_LIMIT,
    "columns": [],
    "labels": {},
    "row_link": {"type": "none"},
    "actions": [],
    "device_filter": {"types": [], "statuses": [], "integrations": [], "areas": []},
    "value_list": [],
    "formats": {},
}

OP_LABELS = {
    "eq": "is", "ne": "is not", "lt": "less than", "le": "at most",
    "gt": "greater than", "ge": "at least", "contains": "contains",
    "not_contains": "does not contain", "starts_with": "starts with",
    "ends_with": "ends with", "in": "is one of (comma list)",
    "not_in": "is not one of (comma list)", "is_empty": "is empty",
    "is_not_empty": "is not empty",
}
JOIN_LABELS = {
    "inner": "Only matching rows (INNER JOIN)",
    "left": "Keep all rows on the left (LEFT OUTER JOIN)",
    "right": "Keep all rows on the right (RIGHT OUTER JOIN)",
    "full": "Keep all rows on both sides (FULL OUTER JOIN)",
}
AGG_LABELS = {
    "count": "Count", "count_distinct": "Count distinct", "min": "Minimum",
    "max": "Maximum", "sum": "Sum", "avg": "Average", "list": "List of values",
}


def _opts(mapping: dict[str, str]) -> list[SelectOptionDict]:
    return [SelectOptionDict(value=k, label=v) for k, v in mapping.items()]


class ComponentFlow(ConfigSubentryFlow):
    """Build a custom device-page component without writing code."""

    def __init__(self) -> None:
        self.spec: dict[str, Any] = copy.deepcopy(EMPTY_SPEC)
        self._pending_source: str | None = None
        self._preview_device: str | None = None
        self._preview: str = ""
        self._remove_join: int | None = None

    # -- helpers ----------------------------------------------------------
    def _col_options(self, include_aggs: bool = False) -> list[SelectOptionDict]:
        opts = [
            SelectOptionDict(value=c, label=query.column_label(self.spec, c))
            for c in query.available_columns(self.spec)
        ]
        if include_aggs:
            opts += [
                SelectOptionDict(value=f"agg:{i}", label=query.column_label(self.spec, f"agg:{i}"))
                for i in range(len(self.spec["aggregates"]))
            ]
        return opts

    def _summary(self) -> str:
        s = self.spec
        lines = []
        src = s["source"]
        if src.get("table"):
            lines.append(f"**From** {query.SCHEMA[src['table']]['label']} ({src['alias']})")
        else:
            lines.append("**From** — not chosen yet")
        for j in s["joins"]:
            lines.append(
                f"**{query.JOIN_TYPES[j['type']]}** {query.SCHEMA[j['table']]['label']} "
                f"({j['alias']}) on `{j['left']}` = `{j['right']}`"
            )
        conds = s["where"]["conditions"]
        if conds:
            joiner = " OR " if s["where"]["match"] == "any" else " AND "
            parts = []
            for c in conds:
                val = (
                    f"this device's {c['value_source'][7:]}"
                    if c.get("value_source", "fixed").startswith("device.")
                    else f"“{c.get('value', '')}”"
                )
                if c["op"] in query.NO_VALUE_OPERATORS:
                    val = ""
                parts.append(f"`{c['column']}` {OP_LABELS[c['op']]} {val}".strip())
            lines.append("**Where** " + joiner.join(parts))
        if s["group_by"]:
            lines.append("**Group by** " + ", ".join(f"`{g}`" for g in s["group_by"]))
        for i, a in enumerate(s["aggregates"]):
            lines.append(f"**Summary {i}** {AGG_LABELS[a['func']]} of `{a['column']}`")
        if s["order_by"]:
            lines.append(
                "**Order by** "
                + ", ".join(f"`{o['column']}` {o['direction']}" for o in s["order_by"])
            )
        lines.append(f"**Limit** {s['limit']}")
        if s["columns"]:
            lines.append("**Shows** " + ", ".join(query.column_label(s, c) for c in s["columns"]))
        if s["actions"]:
            lines.append("**Actions** " + ", ".join(a["label"] for a in s["actions"]))
        f = s["device_filter"]
        scope = [
            f"{k}: {len(v)}" for k, v in f.items() if v
        ]
        lines.append("**Shown on** " + (", ".join(scope) if scope else "every device page"))
        return "\n\n".join(lines)

    def _dependents(self, alias: str) -> list[str]:
        """Things that use columns of an alias (removed with it)."""
        pre = f"{alias}."
        s = self.spec
        out = []
        out += [f"join `{j['alias']}`" for j in s["joins"] if j["left"].startswith(pre) and j["alias"] != alias]
        out += [f"condition on `{c['column']}`" for c in s["where"]["conditions"] if c["column"].startswith(pre)]
        out += [f"group by `{g}`" for g in s["group_by"] if g.startswith(pre)]
        out += [f"summary of `{a['column']}`" for a in s["aggregates"] if a["column"].startswith(pre)]
        out += [f"sort by `{o['column']}`" for o in s["order_by"] if o["column"].startswith(pre)]
        out += [f"column `{c}`" for c in s["columns"] if c.startswith(pre)]
        out += [f"action “{a['label']}”" for a in s["actions"] if (a.get("target_column") or "").startswith(pre)]
        link = s["row_link"]
        if (link.get("column") or "").startswith(pre):
            out.append("row link")
        return out

    def _drop_alias(self, alias: str) -> None:
        pre = f"{alias}."
        s = self.spec
        # Joins that hang off this alias go too (recursively).
        children = [j["alias"] for j in s["joins"] if j["left"].startswith(pre) and j["alias"] != alias]
        s["joins"] = [j for j in s["joins"] if j["alias"] != alias]
        s["where"]["conditions"] = [c for c in s["where"]["conditions"] if not c["column"].startswith(pre)]
        s["group_by"] = [g for g in s["group_by"] if not g.startswith(pre)]
        keep_aggs = [i for i, a in enumerate(s["aggregates"]) if not a["column"].startswith(pre)]
        remap = {f"agg:{old}": f"agg:{new}" for new, old in enumerate(keep_aggs)}
        s["aggregates"] = [s["aggregates"][i] for i in keep_aggs]
        s["order_by"] = [
            {**o, "column": remap.get(o["column"], o["column"])}
            for o in s["order_by"]
            if not o["column"].startswith(pre)
            and (not o["column"].startswith("agg:") or o["column"] in remap)
        ]
        s["columns"] = [
            remap.get(c, c) for c in s["columns"]
            if not c.startswith(pre) and (not c.startswith("agg:") or c in remap)
        ]
        s["labels"] = {k: v for k, v in s["labels"].items() if not k.startswith(pre)}
        s["actions"] = [a for a in s["actions"] if not (a.get("target_column") or "").startswith(pre)]
        if (s["row_link"].get("column") or "").startswith(pre):
            s["row_link"] = {"type": "none"}
        for child in children:
            self._drop_alias(child)

    # -- entry points -----------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """New component: basics first."""
        return await self.async_step_basics(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing component."""
        sub = self._get_reconfigure_subentry()
        self.spec = {**copy.deepcopy(EMPTY_SPEC), **copy.deepcopy(dict(sub.data))}
        return await self.async_step_menu()

    async def async_step_basics(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name, title and look."""
        if user_input is not None:
            self.spec.update(
                name=user_input["name"].strip(),
                title=(user_input.get("title") or user_input["name"]).strip(),
                style=user_input["style"],
                column=_column(user_input.get("column", 1)),
                order=int(user_input["order"]),
                enabled=user_input["enabled"],
                empty_text=user_input.get("empty_text", ""),
            )
            return await self.async_step_menu()
        s = self.spec
        return self.async_show_form(
            step_id="basics",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=s["name"]): TextSelector(),
                    vol.Optional("title", default=s["title"]): TextSelector(),
                    vol.Required("style", default=s["style"]): _select(
                        [SelectOptionDict(value="table", label="Table"),
                         SelectOptionDict(value="list", label="List")],
                        dropdown=False,
                    ),
                    vol.Required(
                        "column", default=str(_column(s.get("column", 1)))
                    ): _column_select(),
                    vol.Required("order", default=s["order"]): NumberSelector(
                        NumberSelectorConfig(min=-100, max=100, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Required("enabled", default=s["enabled"]): BooleanSelector(),
                    vol.Optional("empty_text", default=s["empty_text"]): TextSelector(),
                }
            ),
        )

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Hub of the builder."""
        options = ["basics", "value_list", "source"]
        if self.spec["source"].get("table"):
            options += ["joins", "where", "group", "sort", "columns", "formats", "actions", "devices", "preview"]
        options.append("save")
        return self.async_show_menu(
            step_id="menu",
            menu_options=options,
            description_placeholders={"summary": self._summary()},
        )

    # -- source -------------------------------------------------------------
    async def async_step_source(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """FROM table."""
        if user_input is not None:
            table = user_input["table"]
            current = self.spec["source"].get("table")
            if current and current != table:
                alias = self.spec["source"]["alias"]
                deps = self._dependents(alias) + [f"join `{j['alias']}`" for j in self.spec["joins"]]
                if deps:
                    self._pending_source = table
                    return await self.async_step_source_confirm()
            self.spec["source"] = {"table": table, "alias": "t0"}
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="source",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "table", default=self.spec["source"].get("table", "entities")
                    ): _select(
                        [SelectOptionDict(value=k, label=v["label"]) for k, v in query.SCHEMA.items()]
                    )
                }
            ),
        )

    async def async_step_source_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Changing FROM clears everything built on it."""
        if user_input is not None:
            if user_input.get("confirm"):
                keep = {k: self.spec[k] for k in ("name", "title", "style", "order", "enabled", "empty_text", "device_filter")}
                self.spec = {**copy.deepcopy(EMPTY_SPEC), **keep}
                self.spec["source"] = {"table": self._pending_source, "alias": "t0"}
            self._pending_source = None
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="source_confirm",
            data_schema=vol.Schema({vol.Required("confirm", default=False): BooleanSelector()}),
            description_placeholders={"summary": self._summary()},
        )

    # -- joins --------------------------------------------------------------
    async def async_step_joins(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Join menu."""
        opts = ["add_join"]
        if self.spec["joins"]:
            opts.append("remove_join")
        opts.append("menu")
        return self.async_show_menu(
            step_id="joins", menu_options=opts,
            description_placeholders={"summary": self._summary()},
        )

    async def async_step_add_join(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Join type and table."""
        if user_input is not None:
            self._new_join = {
                "type": user_input["type"],
                "table": user_input["table"],
                "alias": query.next_alias(self.spec),
            }
            return await self.async_step_add_join_on()
        return self.async_show_form(
            step_id="add_join",
            data_schema=vol.Schema(
                {
                    vol.Required("type", default="left"): _select(_opts(JOIN_LABELS), dropdown=False),
                    vol.Required("table"): _select(
                        [SelectOptionDict(value=k, label=v["label"]) for k, v in query.SCHEMA.items()]
                    ),
                }
            ),
        )

    async def async_step_add_join_on(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """ON left = right."""
        new = self._new_join
        right_opts = [
            SelectOptionDict(value=f"{new['alias']}.{c}", label=f"{query.SCHEMA[new['table']]['label']}: {c}")
            for c in query.SCHEMA[new["table"]]["columns"]
        ]
        errors: dict[str, str] = {}
        if user_input is not None:
            join = {**new, "left": user_input["left"], "right": user_input["right"]}
            trial = copy.deepcopy(self.spec)
            trial["joins"].append(join)
            try:
                query.validate(trial)
            except query.QueryError:
                errors["base"] = "invalid_join"
            else:
                self.spec = trial
                return await self.async_step_joins()
        return self.async_show_form(
            step_id="add_join_on",
            data_schema=vol.Schema(
                {
                    vol.Required("left"): _select(self._col_options()),
                    vol.Required("right"): _select(right_opts),
                }
            ),
            errors=errors,
            description_placeholders={"table": query.SCHEMA[new["table"]]["label"]},
        )

    async def async_step_remove_join(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Remove a join (and what depends on it, after a warning)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            alias = user_input["join"]
            deps = self._dependents(alias)
            if deps and not user_input.get("confirm"):
                errors["base"] = "confirm_required"
                self._remove_join_deps = deps
            else:
                self._drop_alias(alias)
                return await self.async_step_joins()
        deps = getattr(self, "_remove_join_deps", [])
        return self.async_show_form(
            step_id="remove_join",
            data_schema=vol.Schema(
                {
                    vol.Required("join"): _select(
                        [
                            SelectOptionDict(
                                value=j["alias"],
                                label=f"{j['alias']}: {query.SCHEMA[j['table']]['label']} on {j['left']} = {j['right']}",
                            )
                            for j in self.spec["joins"]
                        ]
                    ),
                    vol.Required("confirm", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "dependents": "\n".join(f"- {d}" for d in deps) or "(choose a join to see)"
            },
        )

    # -- where --------------------------------------------------------------
    async def async_step_where(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Condition menu."""
        opts = ["add_condition"]
        if self.spec["where"]["conditions"]:
            opts += ["remove_condition", "where_match"]
        opts.append("menu")
        return self.async_show_menu(
            step_id="where", menu_options=opts,
            description_placeholders={"summary": self._summary()},
        )

    async def async_step_add_condition(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """One WHERE condition."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cond = {
                "column": user_input["column"],
                "op": user_input["op"],
                "value_source": user_input["value_source"],
                "value": user_input.get("value", ""),
            }
            if (
                cond["op"] not in query.NO_VALUE_OPERATORS
                and cond["value_source"] == "fixed"
                and cond["value"] == ""
                and cond["op"] not in ("eq", "ne")
            ):
                errors["value"] = "value_required"
            else:
                self.spec["where"]["conditions"].append(cond)
                return await self.async_step_where()
        sources = [SelectOptionDict(value="fixed", label="A value I type")] + [
            SelectOptionDict(value=f"device.{f}", label=f"This device's {f.replace('_', ' ')}")
            for f in query.DEVICE_CONTEXT_FIELDS
        ]
        return self.async_show_form(
            step_id="add_condition",
            data_schema=vol.Schema(
                {
                    vol.Required("column"): _select(self._col_options()),
                    vol.Required("op", default="eq"): _select(_opts(OP_LABELS)),
                    vol.Required("value_source", default="fixed"): _select(sources),
                    vol.Optional("value", default=""): TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_remove_condition(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Remove conditions."""
        conds = self.spec["where"]["conditions"]
        if user_input is not None:
            drop = {int(i) for i in user_input.get("conditions", [])}
            self.spec["where"]["conditions"] = [c for i, c in enumerate(conds) if i not in drop]
            return await self.async_step_where()
        return self.async_show_form(
            step_id="remove_condition",
            data_schema=vol.Schema(
                {
                    vol.Required("conditions", default=[]): _select(
                        [
                            SelectOptionDict(
                                value=str(i),
                                label=f"{c['column']} {OP_LABELS[c['op']]} {c.get('value', '')}",
                            )
                            for i, c in enumerate(conds)
                        ],
                        multiple=True,
                        dropdown=False,
                    )
                }
            ),
        )

    async def async_step_where_match(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """AND / OR."""
        if user_input is not None:
            self.spec["where"]["match"] = user_input["match"]
            return await self.async_step_where()
        return self.async_show_form(
            step_id="where_match",
            data_schema=vol.Schema(
                {
                    vol.Required("match", default=self.spec["where"]["match"]): _select(
                        [SelectOptionDict(value="all", label="All conditions (AND)"),
                         SelectOptionDict(value="any", label="Any condition (OR)")],
                        dropdown=False,
                    )
                }
            ),
        )

    # -- group --------------------------------------------------------------
    async def async_step_group(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Group menu."""
        opts = ["group_by", "add_aggregate"]
        if self.spec["aggregates"]:
            opts.append("remove_aggregate")
        opts.append("menu")
        return self.async_show_menu(
            step_id="group", menu_options=opts,
            description_placeholders={"summary": self._summary()},
        )

    async def async_step_group_by(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """GROUP BY columns."""
        if user_input is not None:
            self.spec["group_by"] = user_input.get("group_by", [])
            return await self.async_step_group()
        return self.async_show_form(
            step_id="group_by",
            data_schema=vol.Schema(
                {
                    vol.Optional("group_by", default=self.spec["group_by"]): _select(
                        self._col_options(), multiple=True
                    )
                }
            ),
        )

    async def async_step_add_aggregate(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """COUNT/MIN/MAX/SUM/AVG/list."""
        if user_input is not None:
            self.spec["aggregates"].append(
                {
                    "func": user_input["func"],
                    "column": user_input["column"],
                    "label": user_input.get("label") or "",
                }
            )
            return await self.async_step_group()
        cols = [SelectOptionDict(value="*", label="Rows (count only)")] + self._col_options()
        return self.async_show_form(
            step_id="add_aggregate",
            data_schema=vol.Schema(
                {
                    vol.Required("func", default="count"): _select(_opts(AGG_LABELS)),
                    vol.Required("column", default="*"): _select(cols),
                    vol.Optional("label", default=""): TextSelector(),
                }
            ),
        )

    async def async_step_remove_aggregate(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Remove one summary column (and its uses)."""
        if user_input is not None:
            idx = int(user_input["aggregate"])
            s = self.spec
            ref = f"agg:{idx}"
            s["aggregates"].pop(idx)

            def shift(r: str) -> str | None:
                if not r.startswith("agg:"):
                    return r
                n = int(r[4:])
                if n == idx:
                    return None
                return f"agg:{n - 1}" if n > idx else r

            s["columns"] = [c for c in (shift(c) for c in s["columns"]) if c]
            s["order_by"] = [
                {**o, "column": shift(o["column"])} for o in s["order_by"] if shift(o["column"])
            ]
            s["labels"].pop(ref, None)
            return await self.async_step_group()
        return self.async_show_form(
            step_id="remove_aggregate",
            data_schema=vol.Schema(
                {
                    vol.Required("aggregate"): _select(
                        [
                            SelectOptionDict(value=str(i), label=query.column_label(self.spec, f"agg:{i}"))
                            for i in range(len(self.spec["aggregates"]))
                        ]
                    )
                }
            ),
        )

    # -- sort / limit -------------------------------------------------------
    async def async_step_sort(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """ORDER BY (up to three) and LIMIT."""
        if user_input is not None:
            order = []
            for n in (1, 2, 3):
                col = user_input.get(f"sort{n}")
                if col and col != "none":
                    order.append({"column": col, "direction": user_input.get(f"dir{n}", "asc")})
            self.spec["order_by"] = order
            self.spec["limit"] = int(user_input["limit"])
            return await self.async_step_menu()
        cols = [SelectOptionDict(value="none", label="—")] + self._col_options(include_aggs=True)
        dirs = [SelectOptionDict(value="asc", label="Ascending"), SelectOptionDict(value="desc", label="Descending")]
        cur = self.spec["order_by"] + [{"column": "none", "direction": "asc"}] * 3
        fields: dict[Any, Any] = {}
        for n in (1, 2, 3):
            fields[vol.Optional(f"sort{n}", default=cur[n - 1]["column"])] = _select(cols)
            fields[vol.Optional(f"dir{n}", default=cur[n - 1]["direction"])] = _select(dirs, dropdown=False)
        fields[vol.Required("limit", default=self.spec["limit"])] = NumberSelector(
            NumberSelectorConfig(min=1, max=query.MAX_LIMIT, mode=NumberSelectorMode.BOX)
        )
        return self.async_show_form(step_id="sort", data_schema=vol.Schema(fields))

    # -- columns ------------------------------------------------------------
    async def async_step_columns(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Displayed columns and what a row opens."""
        errors: dict[str, str] = {}
        if user_input is not None:
            link_type = user_input["link_type"]
            link_col = user_input.get("link_column") or None
            if len(user_input.get("columns") or []) > query.MAX_COLUMNS:
                errors["columns"] = "too_many_columns"
            elif link_type != "none" and not link_col:
                errors["link_column"] = "link_column_required"
            else:
                self.spec["columns"] = user_input.get("columns", [])
                self.spec["formats"] = {
                    k: v for k, v in (self.spec.get("formats") or {}).items()
                    if k in self.spec["columns"]
                }
                self.spec["row_link"] = {"type": link_type, "column": link_col}
                if user_input.get("rename"):
                    return await self.async_step_rename_column()
                return await self.async_step_menu()
        link = self.spec["row_link"]
        return self.async_show_form(
            step_id="columns",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "columns",
                        default=(user_input or {}).get("columns", self.spec["columns"]),
                    ): _select(
                        self._col_options(include_aggs=True), multiple=True
                    ),
                    vol.Required("link_type", default=link.get("type", "none")): _select(
                        [
                            SelectOptionDict(value="none", label="Nothing"),
                            SelectOptionDict(value="entity", label="Entity details"),
                            SelectOptionDict(value="device", label="Device page"),
                            SelectOptionDict(value="area", label="Area page"),
                            SelectOptionDict(value="config_entry", label="Integration entry"),
                            SelectOptionDict(value="url", label="A Home Assistant path in the row"),
                        ]
                    ),
                    vol.Optional("link_column", default=link.get("column") or ""): _select(
                        [SelectOptionDict(value="", label="—")] + self._col_options()
                    ),
                    vol.Optional("rename", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_rename_column(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Header text for one column."""
        if user_input is not None:
            ref = user_input["column"]
            label = (user_input.get("label") or "").strip()
            if ref.startswith("agg:"):
                self.spec["aggregates"][int(ref[4:])]["label"] = label
            elif label:
                self.spec["labels"][ref] = label
            else:
                self.spec["labels"].pop(ref, None)
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="rename_column",
            data_schema=vol.Schema(
                {
                    vol.Required("column"): _select(
                        [
                            SelectOptionDict(value=c, label=query.column_label(self.spec, c))
                            for c in self.spec["columns"]
                        ] or self._col_options(include_aggs=True)
                    ),
                    vol.Optional("label", default=""): TextSelector(),
                }
            ),
        )

    # -- your list ----------------------------------------------------------
    async def async_step_value_list(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """A small lookup table typed into the component: key = label."""
        errors: dict[str, str] = {}
        if user_input is not None:
            rows = query.parse_value_list(user_input.get("lines", ""))
            if len(rows) > query.MAX_LIST_ROWS:
                errors["lines"] = "list_too_long"
            else:
                self.spec["value_list"] = rows
                return await self.async_step_menu()
        return self.async_show_form(
            step_id="value_list",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "lines", default=query.format_value_list(self.spec.get("value_list") or [])
                    ): TextSelector(TextSelectorConfig(multiline=True)),
                }
            ),
            errors=errors,
        )

    # -- column formats -----------------------------------------------------
    async def async_step_formats(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Unit conversion and rounding for one shown column."""
        errors: dict[str, str] = {}
        formats = self.spec.setdefault("formats", {})
        if user_input is not None:
            ref = user_input["column"]
            if user_input.get("remove"):
                formats.pop(ref, None)
                return await self.async_step_menu()
            fmt = {
                "convert_from": user_input.get("convert_from") or "",
                "convert_to": user_input.get("convert_to") or "",
                "decimals": "" if user_input.get("decimals") in (None, "") else int(user_input["decimals"]),
            }
            try:
                query.unit_converter(fmt)
            except query.QueryError:
                errors["base"] = "invalid_format"
            else:
                formats[ref] = fmt
                return await self.async_step_menu()
        units = [SelectOptionDict(value="", label="—")] + [
            SelectOptionDict(value=u, label=u) for u in query.known_units()
        ]
        shown = self.spec["columns"]
        first = shown[0] if shown else ""
        cur = formats.get(first, {})
        return self.async_show_form(
            step_id="formats",
            data_schema=vol.Schema(
                {
                    vol.Required("column", default=first): _select(
                        [SelectOptionDict(value=c, label=query.column_label(self.spec, c)) for c in shown]
                    ),
                    vol.Optional("convert_from", default=cur.get("convert_from", "")): _select(units),
                    vol.Optional("convert_to", default=cur.get("convert_to", "")): _select(units),
                    vol.Optional("decimals"): NumberSelector(
                        NumberSelectorConfig(min=0, max=query.MAX_DECIMALS, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Optional("remove", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "current": "\n".join(
                    f"- {query.column_label(self.spec, k)}: "
                    + (f"{v['convert_from']} → {v['convert_to']}" if v.get("convert_from") else "no conversion")
                    + (f", {v['decimals']} decimals" if v.get("decimals") not in (None, "") else "")
                    for k, v in formats.items()
                ) or "None yet."
            },
        )

    # -- actions ------------------------------------------------------------
    async def async_step_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Row action menu."""
        opts = ["add_action"]
        if self.spec["actions"]:
            opts.append("remove_action")
        opts.append("menu")
        return self.async_show_menu(
            step_id="actions", menu_options=opts,
            description_placeholders={"summary": self._summary()},
        )

    async def async_step_add_action(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """A button on each row that runs a Home Assistant action."""
        errors: dict[str, str] = {}
        if user_input is not None:
            action = user_input["action"]
            level = action_level(action)
            if level == "blocked":
                errors["action"] = "action_blocked"
            elif self.spec["group_by"] or self.spec["aggregates"]:
                if user_input["target_column"] not in self.spec["group_by"]:
                    errors["target_column"] = "target_not_grouped"
            if not errors:
                self.spec["actions"].append(
                    {
                        "label": user_input["label"],
                        "action": action,
                        "target_column": user_input["target_column"],
                        "target_kind": user_input["target_kind"],
                        "data": user_input.get("data") or {},
                        "confirm": bool(user_input.get("confirm")) or level == "confirm",
                        "confirm_text": user_input.get("confirm_text") or "",
                    }
                )
                return await self.async_step_actions()
        return self.async_show_form(
            step_id="add_action",
            data_schema=vol.Schema(
                {
                    vol.Required("label"): TextSelector(),
                    vol.Required("action"): _select(
                        [SelectOptionDict(**o) for o in available_actions(self.hass)]
                    ),
                    vol.Required("target_column"): _select(self._col_options()),
                    vol.Required("target_kind", default="entity_id"): _select(
                        [
                            SelectOptionDict(value="entity_id", label="Entity"),
                            SelectOptionDict(value="device_id", label="Device"),
                            SelectOptionDict(value="area_id", label="Area"),
                        ],
                        dropdown=False,
                    ),
                    vol.Optional("data"): ObjectSelector(),
                    vol.Optional("confirm", default=False): BooleanSelector(),
                    vol.Optional("confirm_text", default=""): TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_remove_action(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Remove row actions."""
        if user_input is not None:
            drop = {int(i) for i in user_input.get("actions", [])}
            self.spec["actions"] = [a for i, a in enumerate(self.spec["actions"]) if i not in drop]
            return await self.async_step_actions()
        return self.async_show_form(
            step_id="remove_action",
            data_schema=vol.Schema(
                {
                    vol.Required("actions", default=[]): _select(
                        [
                            SelectOptionDict(value=str(i), label=f"{a['label']} ({a['action']})")
                            for i, a in enumerate(self.spec["actions"])
                        ],
                        multiple=True,
                        dropdown=False,
                    )
                }
            ),
        )

    # -- device pages -------------------------------------------------------
    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Which device pages show the component."""
        if user_input is not None:
            self.spec["device_filter"] = {
                "types": user_input.get("types", []),
                "statuses": user_input.get("statuses", []),
                "integrations": user_input.get("integrations", []),
                "areas": user_input.get("areas", []),
            }
            return await self.async_step_menu()
        f = self.spec["device_filter"]
        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Optional("types", default=f["types"]): _select(
                        [SelectOptionDict(**o) for o in type_options(self.hass)], multiple=True
                    ),
                    vol.Optional("statuses", default=f["statuses"]): _select(
                        [SelectOptionDict(value=v, label=v.capitalize()) for v in STATUS_VALUES],
                        multiple=True,
                        dropdown=False,
                    ),
                    vol.Optional("integrations", default=f["integrations"]): _select(
                        [SelectOptionDict(**o) for o in integration_options(self.hass)],
                        multiple=True,
                    ),
                    vol.Optional("areas", default=f["areas"]): AreaSelector(
                        AreaSelectorConfig(multiple=True)
                    ),
                }
            ),
        )

    # -- preview & save -----------------------------------------------------
    async def async_step_preview(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Run the query for a chosen device and show the rows."""
        if user_input is not None:
            if user_input.get("done"):
                self._preview = ""
                return await self.async_step_menu()
            self._preview_device = user_input.get("device")
            self._preview = await self._render_preview()
        return self.async_show_form(
            step_id="preview",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "device",
                        description={"suggested_value": self._preview_device},
                    ): DeviceSelector(),
                    vol.Optional("done", default=False): BooleanSelector(),
                }
            ),
            description_placeholders={"result": self._preview or "_Choose a device and submit._"},
        )

    async def _render_preview(self) -> str:
        runtime = self._get_entry().runtime_data
        try:
            result = await runtime.async_run_spec(self.spec, self._preview_device)
        except query.QueryError as err:
            return f"**Error:** {err}"
        if not result.rows:
            return "_No rows._"
        shown = len(self.spec["columns"]) or len(result.columns)
        head = "| " + " | ".join(result.labels[:shown]) + " |"
        sep = "|" + "---|" * shown
        body = [
            "| " + " | ".join(str(v if v is not None else "").replace("|", "\\|")[:60] for v in row[:shown]) + " |"
            for row in result.rows[:20]
        ]
        more = f"\n\n_{len(result.rows)} rows{' (more not shown)' if result.truncated or len(result.rows) > 20 else ''}._"
        return "\n".join([head, sep, *body]) + more

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Validate and store."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if not self.spec["name"]:
            return await self.async_step_basics()
        try:
            query.validate(self.spec)
        except query.QueryError as err:
            errors["base"] = "invalid_query"
            placeholders["error"] = str(err)
        if user_input is not None and errors:
            return await self.async_step_menu()
        if user_input is not None:
            data = copy.deepcopy(self.spec)
            if self.source == "reconfigure":
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=data["name"],
                    data=data,
                )
            return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(
            step_id="save",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={**placeholders, "summary": self._summary()},
            last_step=True,
        )
