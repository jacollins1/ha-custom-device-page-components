import { JSDOM } from "jsdom";
import fs from "fs";
const dom = new JSDOM(`<!DOCTYPE html><body></body>`, { runScripts: "outside-only", pretendToBeVisual: true, url: "http://ha.local/config/devices/device/dev1" });
const w = dom.window;
const calls = [];
const sent = [];
const connection = { sendMessagePromise: async (m) => { sent.push(m); return { context: { id: "real" } }; } };
const hass = { config: { version: "2026.10.4" }, devices: { dev1: {} }, connection,
  entities: { "number.cfg": { device_id: "dev1", entity_category: "config" }, "light.main": { device_id: "dev1", entity_category: null }, "number.other": { device_id: "dev2", entity_category: "config" } },
  states: { "number.cfg": { entity_id: "number.cfg", state: "40", attributes: { friendly_name: "Switch Minimum level", unit_of_measurement: "%" } } },
  callWS: async (m) => { calls.push(m); if (m.type.endsWith("/device")) return { version: "0.1.0", confirm_config_changes: true, components: [
    { id: "related", type: "related", title: "Related", order: 0, related: { helpers: [], entries: [{ title: "Kitchen AL", subtitle: "Adaptive Lighting", link: { kind: "url", url: "/config/integrations/integration/adaptive_lighting#config_entry=x" } }], dashboards: [] } },
    { id: "s1", type: "query", title: "Lights", order: 5, style: "table", empty_text: "", labels: ["Entity", "State"], rows: [{ cells: ["light.a", "on"], key: "k1", link: { kind: "more_info", entity_id: "light.a" } }], actions: [{ index: 0, label: "Toggle", confirm: true, confirm_text: "Sure?" }], truncated: false, error: null },
    { id: "s2", type: "query", title: "Nearby", order: 6, style: "list", empty_text: "", labels: ["Name", "Integration", "Model", "Labels"], rows: [{ cells: ["Fan", "tplink", "", "fan", "c5", "c6", "c7", "c8", "c9"], key: "k2", link: { kind: "url", url: "/config/devices/device/d2" } }], actions: [], truncated: false, error: null },
    { id: "c3", type: "query", title: "RightCol", order: 1, column: 3, style: "list", empty_text: "", labels: ["A"], rows: [{ cells: ["r3"], key: "k3", link: null }], actions: [], truncated: false, error: null },
    { id: "c2", type: "query", title: "MiddleCol", order: 2, column: 2, style: "list", empty_text: "", labels: ["A"], rows: [{ cells: ["r2"], key: "k4", link: null }], actions: [], truncated: false, error: null },
    { id: "mmwave", type: "mmwave", title: "mmWave interference area", order: 30, column: 3, description: "Store what is moving", sent_text: "Sent — watch for the orange LED (~20 s)",
      buttons: [{ command: "generate", label: "Generate interference area", confirm_text: "Scans about 20 seconds, LED pulses orange. Undo: Clear interference area." },
                { command: "clear", label: "Clear interference area", confirm_text: "Removes the stored interference area." }] }] }; return {}; } };
// Fake device page with the layout of the current frontend.
w.eval(`class Page extends HTMLElement {
  constructor(){ super(); this.attachShadow({mode:"open"}); }
  render(cols){ this.shadowRoot.innerHTML = '<hass-subpage><div class="container"><div class="header"></div>' + '<div class="column"></div>'.repeat(cols) + '</div></hass-subpage>'; this.updated(new Map()); }
  updated(){ }
}
customElements.define("ha-config-device-page", Page);`);
w.eval(fs.readFileSync(process.argv[2], "utf8"));
await new Promise(r => setTimeout(r, 20));
const page = w.document.createElement("ha-config-device-page");
page.hass = hass; page.deviceId = "dev1"; w.document.body.appendChild(page);
page.render(3);
await new Promise(r => setTimeout(r, 50));
const card = page.shadowRoot.querySelector(".column cdpc-device-components");
if (!card) throw new Error("card not attached");
if (card.parentNode !== page.shadowRoot.querySelectorAll(".column")[0]) throw new Error("wrong column");
const html = card.shadowRoot.innerHTML;
for (const s of ["Kitchen AL", "Integration entries", "light.a", "Toggle"]) if (!html.includes(s)) throw new Error("missing " + s);
// list style: first column is the link line, the rest are .sub lines (empty skipped, max 8 columns)
const listLi = [...card.shadowRoot.querySelectorAll("ha-card")].find(c => c.textContent.includes("Nearby")).querySelector("li");
const a = listLi.querySelector("a");
if (!a || a.textContent !== "Fan" || a.getAttribute("href") !== "/config/devices/device/d2") throw new Error("list: first column is not the link line");
const subs = [...listLi.querySelectorAll(".sub")].map(e => e.textContent);
if (JSON.stringify(subs) !== JSON.stringify(["tplink", "fan", "c5", "c6", "c7", "c8"])) throw new Error("list: sub lines wrong: " + JSON.stringify(subs));
if (listLi.textContent.includes(" · ")) throw new Error("list: cells still concatenated");
// column placement: one card per column, each showing its own components
const cols3 = [...page.shadowRoot.querySelectorAll(".column")].map(c => c.querySelector("cdpc-device-components").shadowRoot.innerHTML);
if (!cols3[0].includes("Lights") || cols3[0].includes("MiddleCol") || cols3[0].includes("RightCol")) throw new Error("column 1 content wrong");
if (!cols3[1].includes("MiddleCol") || cols3[1].includes("RightCol") || cols3[1].includes("Lights")) throw new Error("column 2 content wrong");
if (!cols3[2].includes("RightCol") || cols3[2].includes("MiddleCol")) throw new Error("column 3 content wrong");
if (calls.filter(c => c.type.endsWith("/device")).length !== 1) throw new Error("components fetched more than once for one page");
const rep = calls.find(c => c.type.endsWith("/attach_report"));
if (!rep || rep.ok !== true) throw new Error("no ok report");
// re-render with 1 column (narrow): card must move into the new first column
page.render(1);
await new Promise(r => setTimeout(r, 20));
if (!page.shadowRoot.querySelector(".column cdpc-device-components")) throw new Error("not re-attached after re-render");
// one column: everything stacks in it, column 1 components first, then 2, then 3
const hosts1 = page.shadowRoot.querySelectorAll("cdpc-device-components");
if (hosts1.length !== 1) throw new Error("expected one card in a one-column page, got " + hosts1.length);
const one = hosts1[0].shadowRoot.innerHTML;
const pos = ["Kitchen AL", "Lights", "Nearby", "MiddleCol", "RightCol"].map(t => one.indexOf(t));
if (pos.some(p => p < 0) || pos.some((p, i) => i && p < pos[i - 1])) throw new Error("one-column order wrong: " + pos);
// two columns: column-3 components go to the last column there is
page.render(2);
await new Promise(r => setTimeout(r, 20));
const cols2 = [...page.shadowRoot.querySelectorAll(".column")].map(c => c.querySelector("cdpc-device-components").shadowRoot.innerHTML);
if (!cols2[1].includes("MiddleCol") || !cols2[1].includes("RightCol") || cols2[0].includes("RightCol")) throw new Error("two-column fallback wrong");
page.render(1);
await new Promise(r => setTimeout(r, 20));
// confirm flow: first click shows confirm, no WS call
const before = calls.length;
card.shadowRoot.querySelector("button[data-run]").click();
await new Promise(r => setTimeout(r, 10));
if (!card.shadowRoot.innerHTML.includes("Sure?") || calls.length !== before) throw new Error("confirm not shown first");
card.shadowRoot.querySelector('button[data-confirmed="1"]').click();
await new Promise(r => setTimeout(r, 20));
const run = calls.find(c => c.type.endsWith("/run_action"));
if (!run || run.confirmed !== true || run.row_key !== "k1") throw new Error("action not sent");
// confirm config changes: config entity of the open device waits for "Save config?"
const root = w.document.createElement("home-assistant"); root.hass = hass; w.document.body.appendChild(root);
const msg = (id, value) => ({ type: "call_service", domain: "number", service: "set_value", service_data: { value }, target: { entity_id: id } });
let p = connection.sendMessagePromise(msg("number.cfg", 55));
await new Promise(r => setTimeout(r, 10));
let dlg = w.document.querySelector(".cdpc-confirm");
if (!dlg || !dlg.textContent.includes("Save config?") || !dlg.textContent.includes("40 %") || !dlg.textContent.includes("55 %")) throw new Error("confirm dialog missing or wrong: " + (dlg && dlg.textContent));
if (sent.length) throw new Error("sent before the answer");
dlg.querySelector('[data-a="cancel"]').click();
const res = await p;
if (sent.length || !res.cancelled) throw new Error("cancel still sent the change");
if (w.document.querySelector(".cdpc-confirm")) throw new Error("dialog not closed");
p = connection.sendMessagePromise(msg("number.cfg", 60));
await new Promise(r => setTimeout(r, 10));
w.document.querySelector('.cdpc-confirm [data-a="save"]').click();
await p;
if (sent.length !== 1 || sent[0].service_data.value !== 60) throw new Error("save did not send");
// hidden page: animation frames never run, Cancel must still finish and restore hass
const realRaf = w.requestAnimationFrame; w.requestAnimationFrame = () => 0;
p = connection.sendMessagePromise(msg("number.cfg", 70));
await new Promise(r => setTimeout(r, 10));
w.document.querySelector('.cdpc-confirm [data-a="cancel"]').click();
const hidden = await Promise.race([p, new Promise(r => setTimeout(() => r("hung"), 1000))]);
if (hidden === "hung" || !hidden.cancelled) throw new Error("cancel hung on a hidden page");
const pg = w.document.querySelector("ha-config-device-page");
if (pg && pg.hass && pg.hass.states["number.cfg"].state !== hass.states["number.cfg"].state) throw new Error("row left on the placeholder value");
w.requestAnimationFrame = realRaf;
if (sent.length !== 1) throw new Error("hidden-page cancel sent the change");
// not intercepted: non-config entity, other device's config entity, other message types
await connection.sendMessagePromise({ type: "call_service", domain: "light", service: "turn_on", target: { entity_id: "light.main" } });
await connection.sendMessagePromise(msg("number.other", 1));
await connection.sendMessagePromise({ type: "get_states" });
if (sent.length !== 4 || w.document.querySelector(".cdpc-confirm")) throw new Error("intercepted something it should not");
// mmWave card: confirmation first, then a confirmed command, then a transient "Sent" note
const mmCard = () => [...page.shadowRoot.querySelectorAll("cdpc-device-components")].map(h => h.shadowRoot).find(r => r.innerHTML.includes("mmWave interference area"));
let mm = mmCard();
if (!mm) throw new Error("mmWave card missing");
const mmBefore = calls.length;
mm.querySelector('button[data-mm="generate"]').click();
await new Promise(r => setTimeout(r, 10));
mm = mmCard();
if (calls.length !== mmBefore) throw new Error("mmWave sent without confirmation");
if (!mm.innerHTML.includes("Scans about 20 seconds")) throw new Error("mmWave confirm text missing");
mm.querySelector('button[data-mm-cancel="generate"]').click();
await new Promise(r => setTimeout(r, 10));
mm = mmCard();
if (calls.length !== mmBefore || mm.innerHTML.includes("Scans about 20 seconds")) throw new Error("mmWave cancel did not cancel");
mm.querySelector('button[data-mm="generate"]').click();
await new Promise(r => setTimeout(r, 10));
mmCard().querySelector('button[data-mm-go="1"]').click();
await new Promise(r => setTimeout(r, 20));
const mmCall = calls.slice(mmBefore).find(c => c.type.endsWith("/mmwave_command"));
if (!mmCall || mmCall.command !== "generate" || mmCall.confirmed !== true || mmCall.device_id !== "dev1") throw new Error("mmWave command wrong: " + JSON.stringify(mmCall));
if (!mmCard().innerHTML.includes("watch for the orange LED")) throw new Error("mmWave sent note missing");
// unknown layout -> failure report
const p2 = w.document.createElement("ha-config-device-page");
p2.hass = hass; p2.deviceId = "dev1"; w.document.body.appendChild(p2);
p2.shadowRoot.innerHTML = "<div>new layout</div>"; p2.updated(new Map());
await new Promise(r => setTimeout(r, 3200));
const fail = calls.find(c => c.type.endsWith("/attach_report") && c.ok === false);
if (!fail) throw new Error("no failure report");
console.log("JS attach tests passed");
