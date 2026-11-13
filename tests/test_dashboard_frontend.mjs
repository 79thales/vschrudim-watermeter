// Testy zápisů Lovelace přes simulované HA API; žádná síť ani produkční zápis.
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

// Malá maketa DOM ověřuje řídicí logiku panelu bez prohlížeče a bez HA.
class FakeNode {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.options = [];
    this.listeners = {};
    this.attributes = {};
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.textContent = "";
    this.dataset = {};
  }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  append(child) {
    this.children.push(child);
    if (this.tagName === "SELECT") this.options.push(child);
    if (this.tagName === "SELECT" && this.options.length === 1) this.value = child.value;
  }
  replaceChildren() { this.children = []; }
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelector(selector) {
    const match = selector.match(/^option\[value="(.*)"\]$/);
    return match ? this.options.find(option => option.value === match[1]) : null;
  }
}
class FakeShadow {
  constructor() { this.nodes = new Map(); this.menu = new FakeNode("ha-menu-button"); }
  set innerHTML(html) {
    this.nodes.clear();
    for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
      const node = new FakeNode(match[1]);
      node.value = match[2].match(/\bvalue="([^"]*)"/)?.[1] || "";
      node.hidden = /\bhidden\b/.test(match[2]);
      node.disabled = /\bdisabled\b/.test(match[2]);
      this.nodes.set(match[3], node);
    }
    for (const match of html.matchAll(/<select\b[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {
      const node = this.nodes.get(match[1]);
      for (const option of match[2].matchAll(/<option\b[^>]*value="([^"]*)"/g)) {
        const child = new FakeNode("option"); child.value = option[1]; node.append(child);
      }
    }
    this.firstChild = true;
  }
  getElementById(id) {
    if (!this.nodes.has(id)) throw new Error("missing DOM id " + id);
    return this.nodes.get(id);
  }
  querySelectorAll() { return []; }
  querySelector(selector) { return selector === "ha-menu-button" ? this.menu : null; }
}
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = new FakeShadow(); return this.shadowRoot; }
};
const registered = new Map();
globalThis.customElements = {
  get: name => registered.get(name),
  define: (name, value) => registered.set(name, value),
};
globalThis.document = {createElement: tag => new FakeNode(tag)};
globalThis.window = {
  location: {search: ""},
  loadCardHelpers: async () => ({createCardElement: async () => new FakeNode("ha-card")}),
};
const modulePath = new URL("../custom_components/vschrudim_watermeter/frontend/dashboard-generator.js", import.meta.url);
const source = await readFile(modulePath);
const dashboard = await import("data:text/javascript;base64," + source.toString("base64"));
const config = {title: "Voda", views: [{path: "voda-chrudim"}, {path: "voda-chrudim-detail", subview: true}]};
const preview = {title: "Voda", url_path: "voda-chrudim", config};

test("panel připraví vlastní horní záložku a náhled nic nezapisuje", async () => {
  const Panel = registered.get("vschrudim-dashboard-generator");
  const panel = new Panel();
  const calls = [];
  const rendered = {
    views: [
      {title: "Voda", path: "voda-vschrudim", sections: [{cards: [{type: "tile"}]}]},
      {title: "Detail", path: "voda-vschrudim-detail", subview: true, sections: [{cards: []}]},
    ],
  };
  panel.hass = {
    language: "cs", user: {is_admin: true},
    callWS: async message => {
      calls.push(message);
      if (message.type.endsWith("/sources")) {
        return {entries: [{entry_id: "synthetic", name: "Místo"}], candidates: [], comparison_integration_states: []};
      }
      if (message.type.endsWith("/preview")) {
        return {config: rendered, yaml: "views: []", missing_entities: [], missing_statistics: [], comparison: false};
      }
      throw new Error("Unexpected write");
    },
  };
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(panel._get("destination").value, "append");
  await panel._generate();
  assert.equal(calls[1].target, "overview");
  assert.deepEqual(calls.map(call => call.type), [
    "vschrudim_watermeter/dashboard/sources", "vschrudim_watermeter/dashboard/preview",
  ]);
  assert.equal(panel._get("visual-preview").attributes.inert, "");
  assert.equal(panel._get("visual-preview").children.length, 2);
  await panel._save();
  assert.equal(calls.length, 2);
});

test("změna vstupu během náhledu zneplatní rozpracovanou odpověď", async () => {
  const Panel = registered.get("vschrudim-dashboard-generator");
  const panel = new Panel();
  let finish;
  panel.hass = {
    language: "cs", user: {is_admin: true},
    callWS: async message => message.type.endsWith("/sources")
      ? {entries: [{entry_id: "synthetic", name: "Místo"}], candidates: [], comparison_integration_states: []}
      : new Promise(resolve => { finish = resolve; }),
  };
  await new Promise(resolve => setImmediate(resolve));
  const pending = panel._generate();
  assert.equal(panel._get("entry").disabled, true);
  panel._invalidate();
  finish({config: {views: []}, yaml: "old", missing_entities: [], missing_statistics: []});
  await pending;
  assert.equal(panel._preview, null);
  assert.equal(panel._get("output").hidden, true);
});

test("srovnávací karta rozlišuje neúplný den od nulové spotřeby", async () => {
  const Card = registered.get("vschrudim-statistics-comparison");
  const card = new Card();
  card.setConfig({source_a: "vschrudim_watermeter:a", source_b: "vschrudim:b", language: "cs"});
  const result = {
    comparable_hours: 0, requested_hours: 1, a_m3: null, b_m3: null, difference_litres: null,
    hours: [{start_local: "2026-03-29T03:00:00+02:00", a_m3: null, b_m3: null,
      difference_m3: null, difference_litres: null, comparable: false, status_a: "missing", status_b: "ok"}],
    days: [{date: "2026-03-29", a_m3: null, b_m3: null, difference_m3: null,
      difference_litres: null, complete: false, comparable_hours: 0, expected_hours: 23}],
    months: [], years: [],
  };
  const calls = [];
  card.hass = {user: {is_admin: true}, callWS: async message => { calls.push(message); return result; }};
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls[0].type, "vschrudim_watermeter/dashboard/compare");
  assert.match(card.shadowRoot.getElementById("status").textContent, /0\/1/);
  const summary = card.shadowRoot.getElementById("summary");
  assert.match(summary.children[0].children[1].textContent, /—/);
  card.shadowRoot.getElementById("resolution").value = "days";
  card._showRows();
  const rows = card.shadowRoot.getElementById("rows");
  assert.match(rows.children[0].children[4].textContent, /0\/23/);
  assert.match(rows.children[0].children[4].textContent, /neúplné/);
});

test("porovnání konfigurace zohledňuje obsah, nikoli pořadí klíčů", () => {
  assert.equal(dashboard.sameConfig({a: 1, b: [2]}, {b: [2], a: 1}), true);
  assert.equal(dashboard.sameConfig({a: 1}, {a: 2}), false);
});

test("ztracená odpověď po úspěšném přidání se ověří opětovným čtením", async () => {
  const calls = [];
  const original = {views: [{path: "home"}], title: "Původní"};
  let reads = 0;
  const api = async message => {
    calls.push(message.type);
    if (message.type === "get_panels") return {lovelace: {config: {mode: "storage"}}};
    if (message.type === "lovelace/config") {
      reads++;
      return reads < 3 ? original : {...original, views: [...original.views, ...config.views]};
    }
    if (message.type === "lovelace/config/save") throw new Error("spojení");
    return {};
  };
  assert.deepEqual(await dashboard.appendViews(api, preview), {view_path: preview.url_path});
  assert.equal(calls.includes("lovelace/config/delete"), false);
});

test("nepotvrzené uložení nemaže původní obsah Přehledu", async () => {
  const calls = [];
  let reads = 0;
  const api = async message => {
    calls.push(message.type);
    if (message.type === "get_panels") return {lovelace: {config: {mode: "storage"}}};
    if (message.type === "lovelace/config") {
      reads++;
      if (reads < 3) return {views: [{path: "home"}]};
      throw new Error("spojení");
    }
    if (message.type === "lovelace/config/save") throw new Error("spojení");
    return {};
  };
  await assert.rejects(dashboard.appendViews(api, preview), /save_unconfirmed/);
  assert.equal(calls.includes("lovelace/config/delete"), false);
});

test("připojení zachová nastavení, existující karty a oba nové pohledy", async () => {
  const old = {title: "Moje", custom: {important: 42}, views: [{path: "home", cards: [{type: "markdown", content: "vlastní"}]}]};
  let saved;
  const api = async message => {
    if (message.type === "get_panels") return {lovelace: {config: {mode: "storage"}}};
    if (message.type === "lovelace/config") return structuredClone(old);
    if (message.type === "lovelace/config/save") { saved = message.config; return {}; }
    throw new Error("unexpected");
  };
  await dashboard.appendViews(api, preview);
  assert.deepEqual(saved.custom, old.custom);
  assert.deepEqual(saved.views, [...old.views, ...config.views]);
  assert.deepEqual(old.views, [{path: "home", cards: [{type: "markdown", content: "vlastní"}]}]);
});

test("kolize, YAML mód a souběžná změna zabrání přepisu", async () => {
  for (const scenario of ["collision", "yaml", "changed"]) {
    let reads = 0;
    let writes = 0;
    const api = async message => {
      if (message.type === "get_panels") return {lovelace: {config: {mode: scenario === "yaml" ? "yaml" : "storage"}}};
      if (message.type === "lovelace/config") {
        reads++;
        return {views: scenario === "collision" ? [{path: "voda-chrudim-detail"}] : [{path: reads === 2 ? "changed" : "home"}]};
      }
      if (message.type === "lovelace/config/save") { writes++; return {}; }
      throw new Error("unexpected");
    };
    await assert.rejects(dashboard.appendViews(api, preview));
    assert.equal(writes, 0);
  }
});

test("při kolizi nevzniká ani resource srovnávací karty", async () => {
  const calls = [];
  const api = async message => {
    calls.push(message.type);
    if (message.type === "get_panels") return {lovelace: {config: {mode: "storage"}}};
    if (message.type === "lovelace/config") return {views: [{path: "voda-chrudim"}]};
    throw new Error("unexpected");
  };
  await assert.rejects(dashboard.appendViews(api, {
    ...preview, comparison: true,
    resource_url: "/vschrudim_watermeter/dashboard-generator.js?v=abc",
  }), /view_exists/);
  assert.equal(calls.some(type => type.startsWith("lovelace/resources")), false);
});

test("resource vzniká jen pro vlastní modul a kolize se nepřepisuje", async () => {
  const url = "/vschrudim_watermeter/dashboard-generator.js?v=abc";
  const calls = [];
  const api = async message => {
    calls.push(message);
    if (message.type === "lovelace/resources/list") return [];
    if (message.type === "lovelace/resources/create") return {};
    throw new Error("unexpected");
  };
  await dashboard.ensureCompareResource(api, url);
  assert.equal(calls[1].url, url);
  await assert.rejects(dashboard.ensureCompareResource(api, "https://example.com/x"), /invalid_resource/);
  const conflicting = async () => [{url, type: "css", id: "other"}];
  await assert.rejects(dashboard.ensureCompareResource(conflicting, url), /resource_collision/);
});
