import { JSDOM } from "jsdom";
import fs from "fs";
const dom = new JSDOM(`<!DOCTYPE html><body></body>`, { runScripts: "outside-only", url: "http://ha.local/config/devices/device/dev1" });
const w = dom.window;
const calls = [];
const hass = { config: { version: "2026.10.4" }, devices: { dev1: {} },
  callWS: async (m) => { calls.push(m); if (m.type.endsWith("/device")) return { version: "0.1.0", components: [
    { id: "related", type: "related", title: "Related", order: 0, related: { helpers: [], entries: [{ title: "Kitchen AL", subtitle: "Adaptive Lighting", link: { kind: "url", url: "/config/integrations/integration/adaptive_lighting#config_entry=x" } }], dashboards: [] } },
    { id: "s1", type: "query", title: "Lights", order: 5, style: "table", empty_text: "", labels: ["Entity", "State"], rows: [{ cells: ["light.a", "on"], key: "k1", link: { kind: "more_info", entity_id: "light.a" } }], actions: [{ index: 0, label: "Toggle", confirm: true, confirm_text: "Sure?" }], truncated: false, error: null },
    { id: "s2", type: "query", title: "Nearby", order: 6, style: "list", empty_text: "", labels: ["Name", "Integration", "Model", "Labels"], rows: [{ cells: ["Fan", "tplink", "", "fan", "c5", "c6", "c7", "c8", "c9"], key: "k2", link: { kind: "url", url: "/config/devices/device/d2" } }], actions: [], truncated: false, error: null },
    { id: "c3", type: "query", title: "RightCol", order: 1, column: 3, style: "list", empty_text: "", labels: ["A"], rows: [{ cells: ["r3"], key: "k3", link: null }], actions: [], truncated: false, error: null },
    { id: "c2", type: "query", title: "MiddleCol", order: 2, column: 2, style: "list", empty_text: "", labels: ["A"], rows: [{ cells: ["r2"], key: "k4", link: null }], actions: [], truncated: false, error: null }] }; return {}; } };
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
// unknown layout -> failure report
const p2 = w.document.createElement("ha-config-device-page");
p2.hass = hass; p2.deviceId = "dev1"; w.document.body.appendChild(p2);
p2.shadowRoot.innerHTML = "<div>new layout</div>"; p2.updated(new Map());
await new Promise(r => setTimeout(r, 3200));
const fail = calls.find(c => c.type.endsWith("/attach_report") && c.ok === false);
if (!fail) throw new Error("no failure report");
console.log("JS attach tests passed");
