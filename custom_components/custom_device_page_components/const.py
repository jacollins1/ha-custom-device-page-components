"""Constants for Custom Device Page Components."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "custom_device_page_components"
NAME: Final = "Custom Device Page Components"
VERSION: Final = "0.1.7"

# Lowest Home Assistant release this version is written and tested for.
MIN_HA_VERSION: Final = "2026.2.0"
# Highest release the device-page attach code has been checked against.
TESTED_HA_VERSION: Final = "2026.10.4"

SUBENTRY_COMPONENT: Final = "component"

# Options of the main entry (built-in components)
CONF_RELATED: Final = "related_enabled"
CONF_RELATED_ORDER: Final = "related_order"
CONF_RELATED_COLUMN: Final = "related_column"
CONF_COLOR_TEMP_KELVIN: Final = "color_temp_kelvin_enabled"
CONF_COLOR_TEMP_MIREDS: Final = "color_temp_mireds_enabled"
# Options of entries created before 0.1.2 (one box, one unit); read only by
# the entry migration.
CONF_COLOR_TEMP: Final = "color_temp_enabled"
CONF_COLOR_TEMP_UNIT: Final = "color_temp_unit"
CONF_COLOR_TEMP_MODELS: Final = "color_temp_models"
CONF_BINDINGS: Final = "bindings_enabled"
CONF_CONFIRM_CONFIG: Final = "confirm_config_changes"

UNIT_KELVIN: Final = "kelvin"
UNIT_MIREDS: Final = "mireds"

DEFAULT_OPTIONS: Final = {
    CONF_RELATED: True,
    CONF_RELATED_ORDER: 0,
    CONF_RELATED_COLUMN: 1,
    CONF_COLOR_TEMP_KELVIN: True,
    CONF_COLOR_TEMP_MIREDS: True,
    CONF_COLOR_TEMP_MODELS: [],
    CONF_BINDINGS: True,
    CONF_CONFIRM_CONFIG: False,
}

STATIC_URL: Final = f"/{DOMAIN}_static"
FRONTEND_FILE: Final = "cdpc.js"

SIGNAL_ATTACH_REPORT: Final = f"{DOMAIN}_attach_report"
SIGNAL_BINDINGS_UPDATED: Final = f"{DOMAIN}_bindings_updated"

ISSUE_ATTACH_FAILED: Final = "attach_failed"

# Unique-id kinds (last "_" segment of a unique id) of the built-in entities.
KIND_CT_MIREDS: Final = "colortemp"
KIND_CT_KELVIN: Final = "colortempk"
KINDS_BINDINGS: Final = ("boundto", "boundfrom", "readbindings", "readall")

# Device page columns a component can be placed in (1 = left).
COLUMNS: Final = (1, 2, 3)
