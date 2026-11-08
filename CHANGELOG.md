# Changelog

## 0.1.9 — 2026-11-07

- New built-in **Inovelli mmWave interference area** card on Inovelli VZM32-SN (ZHA) and VZW32-SN (Z-Wave JS) device pages: *Generate interference area* stores what is moving in the room now (HVAC airflow, fans, curtains) as interference; *Clear interference area* removes it. Always confirmed; runs as the person who pressed it; admin only. Options: on/off (default on) and column (default 3).
- Safety: a narrowly scoped allow for exactly those two commands — ZHA cluster 0xFC32 command 0 with `control_id` 1/3, and Z-Wave parameter 111 with value 1/3 — on the open device only. Every other value (factory reset, reset detection area, …) and every row action using these services stays blocked.

## 0.1.8 — 2026-11-07

- Column formats: **Show as feet and inches** for lengths (e.g. 600 cm → `19′ 8″`, −75 cm → `-2′ 6″`). Decimals apply to the inches.

## 0.1.7 — 2026-11-06

- Builder: **Your list** — a small `key = label` table typed into the component (columns key, label, position). Join it for friendly labels, and sort by position for your own row order.
- Builder: **Column formats** — convert a shown column's numbers between units with Home Assistant's own unit converters (length, temperature, speed, …) and round to N decimals. Text values are left alone.
- Entities table: **setting_key** — the entity's translation key, or its original name as a slug. The same setting gets the same key across integrations (e.g. a ZHA and a Z-Wave Inovelli switch both give `mmwave_depth_minimum_near`).

## 0.1.6 — 2026-11-06

- Group members include nested groups: a device in *Kitchen Lights*, which is inside *All Lights*, is a member of both. New columns `depth` (1 = listed directly) and `via_group_entity_id` / `via_group_name` (the group that lists the member directly). Nesting is followed only through group helpers; loops are cut. An "Automations (Group)" component built from the README recipe therefore also lists automations that use an outer group.

## 0.1.5 — 2026-11-06

- Builder: new **automations** table (name, state, status *Active* / *Inactive* / *Unavailable*, editor link, mode, last triggered) and **group members** table (group entity, name, domain and platform, member entity and its device).
- Builder: *automation and script references* gain a **part** column — `trigger`, `condition` or `action` — so a component can list only automations that trigger or test on something. Script references are `action`.
- With these, a component such as "Automations (Group)" — automations whose triggers or conditions use a group helper this device is a member of — can be built without code (recipe in the README).
- New option **Confirm config changes** (off by default): a change to a control in a device page's *Configuration* section asks "Save config?" first. Cancel sends nothing and resets the control, also when the app is in the background.

## 0.1.4 — 2026-11-05

- Related card: an integration also counts as related when one of its entities names this device's entities as targets in its `entities` or `entity_id` attribute, for example Scheduler, whose schedules (`switch.schedule_*`) live in its own storage rather than in its settings. It is listed under Integration entries as "Used by N: <entity names>". Integrations already linked through their settings keep "Uses this device".

## 0.1.3 — 2026-11-04

Ready for Home Assistant 2026.11, which gives every ZHA group a device of its own:

- Zigbee bindings: only devices with a real IEEE address get *Bound to*, *Bound from* and *Read Zigbee bindings*. ZHA group devices (synthetic identifiers) are skipped, and such entities made by an earlier version are removed on start.
- Color temperature boxes move with their light when the light moves to another device (for example a ZHA group light leaving the coordinator), so they show on the group's own device page.

## 0.1.2 — 2026-11-04

- List style: the first column is the row's link and every other column is its own small grey line under it, styled like the Related card's subtitles. Empty cells are left out. (Previously all columns were joined into one link.)
- A component shows at most 8 columns. The Columns step refuses more, and a stored component with more shows an error asking you to edit it.
- Column placement: each component (in *Basics*) and the Related card (in the options) can go in device page column 1, 2 or 3. On a one-column (narrow) page the cards stack in column order. Existing components stay in column 1. The page's components are fetched once and shared by the column cards.
- Color temperature: the unit choice is replaced by two switches, *(kelvin)* and *(mireds)*, both on for new installs. With both on, each light shows *Color temperature* (mireds) and *Color temperature (kelvin)*. Upgrading keeps the existing box and its entity id: a mireds install also gets the kelvin box; a kelvin install keeps only its kelvin box (turn mireds on in the options if wanted).

## 0.1.1 — 2026-11-04

- Fix: stop using `DeviceRegistry.devices` as a mapping (`.values()`, `.keys()`, `[id]`). Home Assistant 2026.10 logs a deprecation warning for it on every start, and it stops working in 2027.9. The registry is now iterated directly, with a fallback for older releases.
- Tests: any deprecated device-registry access now fails the test suite.
- Repository moved to `jacollins1/ha-custom-device-page-components`.

## 0.1.0 — 2026-11-04

First release.

- Device-page card registered by the integration (no `configuration.yaml` edit), with attach self-check, Repair issue and *Check and reload device page card* button.
- Built-ins: Related helpers / integration entries / dashboards; Color temperature box (Kelvin or mireds); Zigbee bindings for ZHA (bound to, bound from, read, read all).
- No-code component builder (config subentries): FROM, INNER/LEFT/RIGHT/FULL OUTER JOIN, WHERE with device-context values, GROUP BY with summaries, ORDER BY, LIMIT 1–500, row links, row actions with a safety policy, device-page filters by type, status, integration and area, live preview.
- Uses the single-config-entry device model of current Home Assistant, with a fallback for older releases.
