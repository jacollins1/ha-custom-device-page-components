/*
 * Custom Device Page Components - device page card.
 *
 * Loaded on every page by the integration (frontend.add_extra_js_url).
 * It waits for Home Assistant's device page element, adds one card element
 * to each of its columns after every render (each shows the components
 * placed in that column), and tells the integration whether that worked. If a frontend update changes the device page, the card does
 * not appear and Home Assistant shows a Repair issue instead.
 */
const CDPC_VERSION = "0.1.9";
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
  const store = storeFor(page);
  store.hass = hass;
  const hosts = [...root.querySelectorAll(CARD_TAG)];
  columns.forEach((column, index) => {
    let card = hosts.find((h) => h.column === index);
    if (!card) {
      card = document.createElement(CARD_TAG);
      card.column = index;
    }
    // Always the last card of its column, after Home Assistant's own.
    if (card.parentNode !== column || column.lastElementChild !== card) column.appendChild(card);
    card.columns = columns.length;
    card.store = store;
  });
  // The page went from 3 columns to 1 (or back): drop hosts of gone columns.
  for (const h of hosts) if (h.column >= columns.length) h.remove();
  store.setDevice(page.deviceId);
  installConfirm(hass.connection);
  report(hass, true);
}

// One data store per device page, shared by the page's column cards, so the
// device's components are fetched once per load.
function storeFor(page) {
  if (!page.__cdpcStore) page.__cdpcStore = new CdpcStore();
  return page.__cdpcStore;
}

class CdpcStore {
  constructor() {
    this.hass = null;
    this.deviceId = null;
    this.data = null;
    this.error = null;
    this.loading = false;
    this.cards = new Set();
  }

  setDevice(id) {
    if (id === this.deviceId) return;
    this.deviceId = id;
    this.data = null;
    this.error = null;
    this.load();
  }

  async load() {
    if (!this.hass || !this.deviceId) return;
    const id = this.deviceId;
    this.loading = true;
    this.changed();
    try {
      const data = await this.hass.callWS({ type: `${DOMAIN}/device`, device_id: id });
      if (id !== this.deviceId) return;
      this.data = data;
      confirmState.enabled = !!data.confirm_config_changes;
      this.error = null;
    } catch (err) {
      this.error = (err && err.message) || String(err);
    }
    this.loading = false;
    this.changed();
  }

  changed() {
    for (const card of this.cards) card._render();
  }
}

// Column (0-based) a component goes in when the page has `count` columns.
// With fewer columns than chosen, it goes in the last one there is: on a
// one-column (narrow) page everything stacks, column 1 components first.
function columnIndex(comp, count) {
  const wanted = [1, 2, 3].includes(comp.column) ? comp.column : 1;
  return Math.min(wanted, count) - 1;
}


// ---------------------------------------------------------------------------
// "Confirm config changes" (integration option, off by default).
// A change to an entity in the Configuration section of the device page that
// is open (entity_category "config", same device) is held until the user
// answers "Save config?". The hook sits on the websocket connection, so every
// row type (slider, box, select, toggle, button) is covered. Any error in
// here lets the change through unchanged: the option must never block HA.
// ---------------------------------------------------------------------------
const confirmState = { enabled: false, queue: Promise.resolve() };
const DEVICE_PATH = /^\/config\/devices\/device\/([^/?#]+)/;

function rootHass() {
  const ha = document.querySelector("home-assistant");
  return ha && ha.hass;
}

function configTarget(msg) {
  if (!confirmState.enabled || !msg || msg.type !== "call_service") return null;
  const m = DEVICE_PATH.exec(location.pathname);
  const hass = rootHass();
  if (!m || !hass || !hass.entities) return null;
  let ids = (msg.target && msg.target.entity_id) || (msg.service_data && msg.service_data.entity_id);
  if (!ids) return null;
  ids = Array.isArray(ids) ? ids : [ids];
  const hit = ids.find((id) => {
    const e = hass.entities[id];
    return e && e.device_id === m[1] && e.entity_category === "config";
  });
  return hit ? { entityId: hit, hass } : null;
}

function describe(hit, msg) {
  const st = hit.hass.states[hit.entityId];
  const unit = (st && st.attributes.unit_of_measurement) || "";
  const fmt = (v) => (v === undefined || v === null ? "?" : `${v}${unit && /^-?[\d.]+$/.test(String(v)) ? " " + unit : ""}`);
  const data = msg.service_data || {};
  let next;
  if ("value" in data) next = fmt(data.value);
  else if ("option" in data) next = data.option;
  else if (msg.service === "turn_on") next = "on";
  else if (msg.service === "turn_off") next = "off";
  else if (msg.service === "toggle") next = st && st.state === "on" ? "off" : "on";
  else next = msg.service.replace(/_/g, " ");
  const name = (st && st.attributes.friendly_name) || hit.entityId;
  return { name, from: st ? fmt(st.state) : "?", to: next };
}

function askSave(info) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "cdpc-confirm";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.style.cssText =
      "position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;" +
      "background:rgba(0,0,0,.45);font-family:var(--ha-font-family-body, Roboto, sans-serif)";
    wrap.innerHTML = `<div style="background:var(--card-background-color,#fff);color:var(--primary-text-color,#212121);
      border-radius:16px;padding:20px 24px;max-width:360px;width:calc(100% - 48px);box-shadow:0 8px 24px rgba(0,0,0,.3)">
      <div style="font-size:20px;margin-bottom:12px">Save config?</div>
      <div style="font-size:14px;margin-bottom:4px">${esc(info.name)}</div>
      <div style="font-size:14px;color:var(--secondary-text-color,#727272);margin-bottom:20px">${esc(info.from)} → ${esc(info.to)}</div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button data-a="cancel" style="font:inherit;font-size:14px;padding:8px 16px;border-radius:18px;border:1px solid var(--primary-color,#03a9f4);background:none;color:var(--primary-color,#03a9f4);cursor:pointer">Cancel</button>
        <button data-a="save" style="font:inherit;font-size:14px;padding:8px 16px;border-radius:18px;border:none;background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff);cursor:pointer">Save</button>
      </div></div>`;
    const done = (save) => {
      document.removeEventListener("keydown", onKey, true);
      wrap.remove();
      resolve(save);
    };
    const onKey = (ev) => {
      if (ev.key === "Escape") { ev.stopPropagation(); done(false); }
    };
    wrap.addEventListener("click", (ev) => {
      const a = ev.target && ev.target.getAttribute && ev.target.getAttribute("data-a");
      if (a) done(a === "save");
      else if (ev.target === wrap) done(false); // tap outside = Cancel
    });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(wrap);
    const save = wrap.querySelector('[data-a="save"]');
    if (save) save.focus();
  });
}

// Put the control back to the entity's current value: rows only redraw a
// control when the value they render changes, so render a different value
// once, then the real one.
async function revertRows(entityId) {
  const hass = rootHass();
  const page = findPage(document);
  if (!hass || !page || !hass.states[entityId]) return;
  const real = hass.states[entityId];
  const flip = real.state === "on" ? "off" : real.state === "off" ? "on" : "unavailable";
  page.hass = { ...hass, states: { ...hass.states, [entityId]: { ...real, state: flip } } };
  try {
    await page.updateComplete;
    // A hidden page (backgrounded app or tab) never runs animation frames,
    // so don't wait on one for longer than a moment.
    await new Promise((r) => {
      const t = setTimeout(r, 100);
      requestAnimationFrame(() => {
        clearTimeout(t);
        r();
      });
    });
  } finally {
    page.hass = rootHass() || hass;
  }
}

function findPage(root) {
  const direct = root.querySelector(PAGE_TAG);
  if (direct) return direct;
  for (const el of root.querySelectorAll("*")) {
    if (el.shadowRoot) {
      const found = findPage(el.shadowRoot);
      if (found) return found;
    }
  }
  return null;
}

function installConfirm(conn) {
  if (!conn || conn.__cdpcConfirm || typeof conn.sendMessagePromise !== "function") return;
  const send = conn.sendMessagePromise.bind(conn);
  conn.sendMessagePromise = function (msg) {
    let hit = null;
    try {
      hit = configTarget(msg);
    } catch (err) {
      hit = null;
    }
    if (!hit) return send(msg);
    const run = confirmState.queue.then(async () => {
      let save = true;
      try {
        save = await askSave(describe(hit, msg));
      } catch (err) {
        save = true;
      }
      if (save) return send(msg);
      try {
        await revertRows(hit.entityId);
      } catch (err) {
        /* the next state update redraws the row anyway */
      }
      return { context: null, cancelled: true };
    });
    confirmState.queue = run.catch(() => {});
    return run;
  };
  conn.__cdpcConfirm = true;
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
    this.column = 0; // 0-based column of the device page this card sits in
    this.columns = 1; // how many columns the page has right now
    this._store = null;
    this._pending = {}; // "comp|action|row" -> confirming
    // ShadowRoot has no onclick property; listen once for every render.
    this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
  }

  set store(store) {
    if (store === this._store) return;
    if (this._store) this._store.cards.delete(this);
    this._store = store;
    store.cards.add(this);
    this._render();
  }

  get _hass() {
    return this._store && this._store.hass;
  }

  get _deviceId() {
    return this._store && this._store.deviceId;
  }

  _load() {
    return this._store ? this._store.load() : Promise.resolve();
  }

  disconnectedCallback() {
    if (this._store && !this.isConnected) this._store.cards.delete(this);
  }

  connectedCallback() {
    if (this._store) {
      this._store.cards.add(this);
      this._render();
    }
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

  // Inovelli mmWave: Generate / Clear interference area. Always confirmed;
  // after the switch acknowledges, a short "Sent" note (the switch reports
  // nothing back about the stored map, so nothing more is claimed).
  _mmwave(comp) {
    const st = this._mmState || (this._mmState = {});
    const desc = comp.description ? `<div class="note" style="margin:0 0 8px">${esc(comp.description)}</div>` : "";
    const buttons = comp.buttons
      .map((b) => {
        const s = st[b.command];
        if (s === "confirm")
          return `<div class="confirm" style="margin:8px 0">${esc(b.confirm_text)}</div>
            <button class="danger" data-mm="${esc(b.command)}" data-mm-go="1">${esc(b.label)}</button>
            <button data-mm-cancel="${esc(b.command)}">Cancel</button>`;
        if (s === "busy") return `<button disabled>${esc(b.label)}…</button>`;
        return `<button data-mm="${esc(b.command)}">${esc(b.label)}</button>`;
      })
      .join(" ");
    const note = st.sent ? `<div class="note">${esc(comp.sent_text)}</div>` : "";
    const err = st.error ? `<div class="error">${esc(st.error)}</div>` : "";
    return `${desc}<div class="acts">${buttons}</div>${note}${err}`;
  }

  async _mmClick(t) {
    const st = this._mmState || (this._mmState = {});
    if (t.dataset.mmCancel) {
      delete st[t.dataset.mmCancel];
      this._render();
      return;
    }
    const command = t.dataset.mm;
    if (t.dataset.mmGo !== "1") {
      for (const k of ["generate", "clear"]) if (st[k] === "confirm") delete st[k];
      st[command] = "confirm";
      st.sent = false;
      st.error = null;
      this._render();
      return;
    }
    st[command] = "busy";
    this._render();
    try {
      await this._hass.callWS({
        type: `${DOMAIN}/mmwave_command`,
        device_id: this._deviceId,
        command,
        confirmed: true,
      });
      st.sent = true;
      clearTimeout(this._mmTimer);
      this._mmTimer = setTimeout(() => {
        st.sent = false;
        this._render();
      }, 30000);
    } catch (err) {
      st.error = (err && err.message) || String(err);
    }
    delete st[command];
    this._render();
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
    const store = this._store;
    let html = `<style>${STYLE}</style>`;
    if (!store) {
      root.innerHTML = html;
      return;
    }
    if (store.error) {
      if (this.column === 0)
        html += `<ha-card outlined><div class="card-header">Device page components</div>
          <div class="content error">${esc(store.error)}</div></ha-card>`;
    } else if (!store.data) {
      if (store.loading && this.column === 0)
        html += `<ha-card outlined><div class="card-header">Related</div><div class="content empty">Loading…</div></ha-card>`;
    } else {
      const count = this.columns;
      // Server order is by position; within one stacked (narrow) column the
      // cards follow their chosen column first, like Home Assistant's own.
      const mine = store.data.components
        .map((comp, i) => ({ comp, i }))
        .filter(({ comp }) => columnIndex(comp, count) === this.column)
        .sort((a, b) => (a.comp.column || 1) - (b.comp.column || 1) || a.i - b.i)
        .map(({ comp }) => comp);
      html += mine
        .map(
          (comp, i) => `<ha-card outlined><div class="card-header"><span>${esc(comp.title)}</span>
            ${i === 0 ? `<button class="refresh" title="Refresh" data-refresh="1">↻</button>` : ""}</div>
            <div class="content">${comp.type === "related" ? this._related(comp) : comp.type === "mmwave" ? this._mmwave(comp) : this._query(comp)}</div></ha-card>`
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
    if (t.dataset.mm || t.dataset.mmCancel) {
      await this._mmClick(t);
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
