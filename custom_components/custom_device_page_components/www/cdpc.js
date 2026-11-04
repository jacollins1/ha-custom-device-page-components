/*
 * Custom Device Page Components - device page card.
 *
 * Loaded on every page by the integration (frontend.add_extra_js_url).
 * It waits for Home Assistant's device page element, adds one card element
 * to its first column after each render, and tells the integration whether
 * that worked. If a frontend update changes the device page, the card does
 * not appear and Home Assistant shows a Repair issue instead.
 */
const CDPC_VERSION = "0.1.2";
// Displayed columns per component; the server enforces the same cap.
const MAX_COLUMNS = 8;
const DOMAIN = "custom_device_page_components";
const PAGE_TAG = "ha-config-device-page";
const CARD_TAG = "cdpc-device-components";

const reported = { ok: false, fail: false };

function report(hass, ok, reason) {
  if (!hass || (ok ? reported.ok : reported.fail)) return;
  if (ok) reported.ok = true;
  else reported.fail = true;
  hass
    .callWS({
      type: `${DOMAIN}/attach_report`,
      ok,
      reason: reason || null,
      ha_version: (hass.config && hass.config.version) || null,
      module: CDPC_VERSION,
    })
    .catch(() => {});
}

function navigate(path) {
  const from = location.pathname + location.search + location.hash;
  history.pushState({ ...(history.state || {}), from }, "", path);
  window.dispatchEvent(
    new CustomEvent("location-changed", { detail: { replace: false } })
  );
}

function attach(page) {
  const hass = page.hass;
  const root = page.shadowRoot;
  if (!hass || !root || !page.deviceId) return;
  // The device page renders nothing useful until the device is known.
  if (!hass.devices || !hass.devices[page.deviceId]) return;
  const columns = root.querySelectorAll(".container > .column");
  if (!columns.length) {
    // Rendered, but not in the layout this version knows: check again once
    // the render settles before calling it a failure.
    clearTimeout(page.__cdpcFailTimer);
    page.__cdpcFailTimer = setTimeout(() => {
      if (!page.isConnected) return;
      if (!page.shadowRoot.querySelectorAll(".container > .column").length) {
        report(hass, false, "Device page layout not recognised (no .container > .column)");
      }
    }, 3000);
    return;
  }
  clearTimeout(page.__cdpcFailTimer);
  let card = root.querySelector(CARD_TAG);
  if (!card) card = document.createElement(CARD_TAG);
  if (card.parentNode !== columns[0]) columns[0].appendChild(card);
  card.hass = hass;
  card.deviceId = page.deviceId;
  report(hass, true);
}

async function install() {
  await customElements.whenDefined(PAGE_TAG);
  const cls = customElements.get(PAGE_TAG);
  if (cls.prototype.__cdpcPatched) return;
  const original = cls.prototype.updated;
  cls.prototype.updated = function (changed) {
    if (original) original.call(this, changed);
    try {
      attach(this);
    } catch (err) {
      report(this.hass, false, `Attach error: ${err && err.message}`);
    }
  };
  cls.prototype.__cdpcPatched = true;
  // A page already on screen picks the card up on its next render, which
  // happens with the next state change.
}

const STYLE = `
  :host { display: block; }
  ha-card { margin-bottom: 16px; display: block; }
  .card-header { display: flex; align-items: center; justify-content: space-between;
    padding: 16px 16px 8px; font-size: var(--ha-card-header-font-size, 20px);
    font-weight: var(--ha-font-weight-normal, 400); color: var(--ha-card-header-color, var(--primary-text-color)); }
  .content { padding: 0 16px 16px; }
  h3 { font-size: 14px; font-weight: 500; margin: 12px 0 4px; color: var(--secondary-text-color);
    text-transform: uppercase; letter-spacing: .04em; }
  ul { list-style: none; margin: 0; padding: 0; }
  li { padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
  li:last-child { border-bottom: none; }
  a, .link { color: var(--primary-color); text-decoration: none; cursor: pointer; }
  a:hover, .link:hover { text-decoration: underline; }
  .sub { display: block; font-size: 12px; color: var(--secondary-text-color); }
  .empty { color: var(--secondary-text-color); font-size: 14px; }
  .error { color: var(--error-color); font-size: 14px; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th { text-align: left; font-weight: 500; color: var(--secondary-text-color);
    padding: 6px 8px 6px 0; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  td { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  button { font: inherit; font-size: 13px; padding: 4px 10px; margin: 2px 4px 2px 0;
    border-radius: 16px; border: 1px solid var(--primary-color); background: none;
    color: var(--primary-color); cursor: pointer; }
  button.danger { border-color: var(--error-color); color: var(--error-color); }
  button[disabled] { opacity: .5; cursor: default; }
  .refresh { border: none; font-size: 18px; padding: 0 4px; }
  .confirm { font-size: 13px; color: var(--primary-text-color); }
  .note { font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; }
  .acts { margin-top: 4px; }
`;

const esc = (v) =>
  String(v === null || v === undefined ? "" : v).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

class CdpcDeviceComponents extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._deviceId = null;
    this._data = null;
    this._error = null;
    this._pending = {}; // "comp|action|row" -> confirming
    this._loading = false;
    // ShadowRoot has no onclick property; listen once for every render.
    this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
  }

  set hass(h) {
    this._hass = h;
  }

  set deviceId(id) {
    if (id === this._deviceId) return;
    this._deviceId = id;
    this._data = null;
    this._load();
  }

  connectedCallback() {
    if (this._deviceId && !this._data && !this._loading) this._load();
  }

  async _load() {
    if (!this._hass || !this._deviceId) return;
    const id = this._deviceId;
    this._loading = true;
    this._render();
    try {
      const data = await this._hass.callWS({ type: `${DOMAIN}/device`, device_id: id });
      if (id !== this._deviceId) return;
      this._data = data;
      this._error = null;
    } catch (err) {
      this._error = (err && err.message) || String(err);
    }
    this._loading = false;
    this._render();
  }

  _linkHtml(link, text) {
    if (!link) return esc(text);
    if (link.kind === "more_info")
      return `<span class="link" data-more="${esc(link.entity_id)}">${esc(text)}</span>`;
    return `<a href="${esc(link.url)}" data-nav="${esc(link.url)}">${esc(text)}</a>`;
  }

  _related(comp) {
    const sections = [
      ["helpers", "Helpers"],
      ["entries", "Integration entries"],
      ["dashboards", "Dashboards"],
    ];
    const total = sections.reduce((n, [k]) => n + (comp.related[k] || []).length, 0);
    if (!total) return `<div class="empty">No helpers, integration entries or dashboards reference this device.</div>`;
    return sections
      .filter(([k]) => (comp.related[k] || []).length)
      .map(
        ([k, label]) => `<h3>${label}</h3><ul>${comp.related[k]
          .map(
            (r) =>
              `<li>${this._linkHtml(r.link, r.title)}${r.subtitle ? `<span class="sub">${esc(r.subtitle)}</span>` : ""}</li>`
          )
          .join("")}</ul>`
      )
      .join("");
  }

  _actions(comp, row) {
    if (!comp.actions.length) return "";
    return comp.actions
      .map((a) => {
        const key = `${comp.id}|${a.index}|${row.key}`;
        const state = this._pending[key];
        if (state === "confirm")
          return `<span class="confirm">${esc(a.confirm_text)}</span>
            <button class="danger" data-run="${esc(key)}" data-confirmed="1">Yes</button>
            <button data-cancel="${esc(key)}">Cancel</button>`;
        if (state === "busy") return `<button disabled>${esc(a.label)}…</button>`;
        return `<button class="${a.confirm ? "danger" : ""}" data-run="${esc(key)}"
          data-confirm="${a.confirm ? 1 : 0}">${esc(a.label)}</button>`;
      })
      .join("");
  }

  _query(comp) {
    if (comp.error) return `<div class="error">${esc(comp.error)}</div>`;
    if (!comp.rows.length) return `<div class="empty">${esc(comp.empty_text)}</div>`;
    const more = comp.truncated ? `<div class="note">More rows exist; raise the limit to see them.</div>` : "";
    if (comp.style === "list") {
      // Like the Related card: the first column is the row's link line; every
      // further column is its own small secondary line under it.
      return `<ul>${comp.rows
        .map((row) => {
          const [first, ...rest] = row.cells.slice(0, MAX_COLUMNS);
          const title = first === null || first === undefined || first === "" ? "—" : first;
          const subs = rest
            .filter((c) => c !== null && c !== undefined && c !== "")
            .map((c) => `<span class="sub">${esc(c)}</span>`)
            .join("");
          const acts = this._actions(comp, row);
          return `<li>${this._linkHtml(row.link, title)}${subs}${acts ? `<div class="acts">${acts}</div>` : ""}</li>`;
        })
        .join("")}</ul>${more}`;
    }
    const head = comp.labels.slice(0, MAX_COLUMNS).map((l) => `<th>${esc(l)}</th>`).join("") + (comp.actions.length ? "<th></th>" : "");
    const body = comp.rows
      .map(
        (row) =>
          `<tr>${row.cells
            .slice(0, MAX_COLUMNS)
            .map((c, i) => `<td>${i === 0 ? this._linkHtml(row.link, c) : esc(c)}</td>`)
            .join("")}${comp.actions.length ? `<td>${this._actions(comp, row)}</td>` : ""}</tr>`
      )
      .join("");
    return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
  }

  _render() {
    const root = this.shadowRoot;
    let html = `<style>${STYLE}</style>`;
    if (this._error) {
      html += `<ha-card outlined><div class="card-header">Device page components</div>
        <div class="content error">${esc(this._error)}</div></ha-card>`;
    } else if (!this._data) {
      html += this._loading
        ? `<ha-card outlined><div class="card-header">Related</div><div class="content empty">Loading…</div></ha-card>`
        : "";
    } else {
      html += this._data.components
        .map(
          (comp, i) => `<ha-card outlined><div class="card-header"><span>${esc(comp.title)}</span>
            ${i === 0 ? `<button class="refresh" title="Refresh" data-refresh="1">↻</button>` : ""}</div>
            <div class="content">${comp.type === "related" ? this._related(comp) : this._query(comp)}</div></ha-card>`
        )
        .join("");
    }
    root.innerHTML = html;
  }

  async _click(ev) {
    const t = ev.composedPath()[0];
    if (!(t instanceof HTMLElement)) return;
    if (t.dataset.nav) {
      ev.preventDefault();
      navigate(t.dataset.nav);
      return;
    }
    if (t.dataset.more) {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: { entityId: t.dataset.more },
          bubbles: true,
          composed: true,
        })
      );
      return;
    }
    if (t.dataset.refresh) {
      this._load();
      return;
    }
    if (t.dataset.cancel) {
      delete this._pending[t.dataset.cancel];
      this._render();
      return;
    }
    if (t.dataset.run) {
      const key = t.dataset.run;
      if (t.dataset.confirm === "1") {
        this._pending[key] = "confirm";
        this._render();
        return;
      }
      const [componentId, actionIndex, ...rest] = key.split("|");
      this._pending[key] = "busy";
      this._render();
      try {
        await this._hass.callWS({
          type: `${DOMAIN}/run_action`,
          device_id: this._deviceId,
          component_id: componentId,
          action_index: Number(actionIndex),
          row_key: rest.join("|"),
          confirmed: t.dataset.confirmed === "1",
        });
        delete this._pending[key];
        await this._load();
      } catch (err) {
        delete this._pending[key];
        this._render();
        alert((err && err.message) || String(err));
      }
    }
  }
}

if (!customElements.get(CARD_TAG)) customElements.define(CARD_TAG, CdpcDeviceComponents);
window.__cdpcVersion = CDPC_VERSION;
install();
