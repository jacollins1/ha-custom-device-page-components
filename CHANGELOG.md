# Changelog

## 0.1.2 — 2026-11-04

- List style: the first column is the row's link and every other column is its own small grey line under it, styled like the Related card's subtitles. Empty cells are left out. (Previously all columns were joined into one link.)
- A component shows at most 8 columns. The Columns step refuses more, and a stored component with more shows an error asking you to edit it.

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
