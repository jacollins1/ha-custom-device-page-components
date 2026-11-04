# Changelog

## 0.1.0 — 2026-11-04

First release.

- Device-page card registered by the integration (no `configuration.yaml` edit), with attach self-check, Repair issue and *Check and reload device page card* button.
- Built-ins: Related helpers / integration entries / dashboards; Color temperature box (Kelvin or mireds); Zigbee bindings for ZHA (bound to, bound from, read, read all).
- No-code component builder (config subentries): FROM, INNER/LEFT/RIGHT/FULL OUTER JOIN, WHERE with device-context values, GROUP BY with summaries, ORDER BY, LIMIT 1–500, row links, row actions with a safety policy, device-page filters by type, status, integration and area, live preview.
- Uses the single-config-entry device model of current Home Assistant, with a fallback for older releases.
