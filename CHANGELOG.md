# Changelog

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
