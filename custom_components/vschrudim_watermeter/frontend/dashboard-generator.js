/* Místní panel a karta využívají přihlášené spojení HA, bez tokenů a CDN. */
const DOMAIN = "vschrudim_watermeter";
const MODULE_BASE = "/vschrudim_watermeter/dashboard-generator.js";

function sameConfig(left, right) {
  // Zahrnujeme i cizí klíče; pořadí klíčů v JSON nehraje roli.
  const canonical = value => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === "object"
      ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
      : value;
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

async function appendViews(callWS, preview) {
  // Zachováme všechny původní klíče i pohledy. YAML režim nepřepisujeme.
  const views = preview.config?.views;
  if (!Array.isArray(views) || !views.length) throw new Error("invalid_views");
  const panels = await callWS({type: "get_panels"});
  if (panels.lovelace?.config?.mode === "yaml") throw new Error("yaml_dashboard");
  let current;
  try { current = await callWS({type: "lovelace/config"}); }
  catch { throw new Error("overview_not_editable"); }
  if (!Array.isArray(current?.views)) throw new Error("overview_not_editable");
  const paths = views.map(view => view.path);
  if (new Set(paths).size !== paths.length ||
      paths.some(path => !path || current.views.some(view => view.path === path))) {
    throw new Error("view_exists");
  }
  const merged = {...current, views: [...current.views, ...views]};
  // Nejdřív ověříme, že lze pohled připojit. Až potom případně založíme
  // resource; v YAML režimu či při kolizi tak nevznikne zbytečný zápis.
  if (preview.comparison) await ensureCompareResource(callWS, preview.resource_url);
  const latest = await callWS({type: "lovelace/config"});
  if (!sameConfig(current, latest)) throw new Error("overview_changed");
  // Poslední čtení a zápis nejsou atomické; souběh dokumentujeme uživateli.
  try { await callWS({type: "lovelace/config/save", config: merged}); }
  catch {
    try {
      const saved = await callWS({type: "lovelace/config"});
      if (sameConfig(saved, merged)) return {view_path: paths[0]};
    } catch { /* Nejasný výsledek není důvod k mazání. */ }
    throw new Error("save_unconfirmed");
  }
  return {view_path: paths[0]};
}

async function ensureCompareResource(callWS, resourceUrl) {
  // Registraci vlastní lokální karty vyvoláme až po potvrzení zápisu.
  if (typeof resourceUrl !== "string" || !resourceUrl.startsWith(MODULE_BASE + "?v=")) {
    throw new Error("invalid_resource");
  }
  const resources = await callWS({type: "lovelace/resources/list"});
  const matches = resources.filter(item => item.url?.split("?")[0] === MODULE_BASE);
  if (matches.length > 1 || (matches.length && matches[0].type !== "module")) {
    throw new Error("resource_collision");
  }
  if (matches[0]?.url === resourceUrl) return;
  try {
    if (matches.length) {
      await callWS({
        type: "lovelace/resources/update", resource_id: matches[0].id,
        res_type: "module", url: resourceUrl,
      });
    } else {
      await callWS({type: "lovelace/resources/create", res_type: "module", url: resourceUrl});
    }
  } catch {
    try {
      const reread = await callWS({type: "lovelace/resources/list"});
      if (reread.some(item => item.url === resourceUrl && item.type === "module")) return;
    } catch { /* Nelze ověřit; opakovaný zápis se neprovádí. */ }
    throw new Error("resource_unconfirmed");
  }
}

function downloadText(text, filename, mime) {
  const url = URL.createObjectURL(new Blob([text], {type: mime}));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function addText(parent, tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  parent.append(node);
  return node;
}

class VsChrudimComparisonCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._sequence = 0;
    this._lastHour = null;
    this._days = 7;
  }
  setConfig(config) {
    if (!config?.source_a || !config?.source_b) throw new Error("Chybí zdroje statistik.");
    this._config = config;
    this._sequence++;
    this._lastHour = null;
    if (this._hass) this._load();
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const hour = Math.floor(Date.now() / 3600000);
    if (hour !== this._lastHour) {
      this._lastHour = hour;
      this._load();
    }
  }
  getCardSize() { return 6; }
  _text(cs, en) { return this._config?.language === "en" ? en : cs; }
  _render() {
    if (this.shadowRoot.firstChild) return;
    this.shadowRoot.innerHTML = [
      "<style>ha-card{padding:16px} h3{margin:0 0 12px}.top{display:flex;gap:10px;align-items:center;flex-wrap:wrap}",
      ".summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin:12px 0}",
      ".box{padding:10px;background:var(--secondary-background-color);border-radius:8px}",
      "table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:5px;border-bottom:1px solid var(--divider-color)}",
      ".scroll{overflow:auto;max-height:280px}button,select{font:inherit;padding:6px}p{color:var(--secondary-text-color)}</style>",
      '<ha-card><h3 id="title"></h3><div class="top"><label id="period-label"></label>',
      '<select id="period"><option value="3">3</option><option value="7">7</option><option value="31">31</option><option value="365">365</option></select>',
      '<label id="start-label"></label><input id="start" type="datetime-local" step="3600">',
      '<label id="end-label"></label><input id="end" type="datetime-local" step="3600">',
      '<button id="refresh"></button><button id="export" disabled></button></div>',
      '<p id="status" aria-live="polite"></p><div class="summary" id="summary"></div>',
      '<p id="note"></p><label id="resolution-label"></label><select id="resolution">',
      '<option value="hours"></option><option value="days"></option><option value="months"></option><option value="years"></option></select>',
      '<div class="scroll"><table><thead><tr id="heads"></tr></thead><tbody id="rows"></tbody></table></div>',
      '<p id="more"></p></ha-card>',
    ].join("");
    const get = id => this.shadowRoot.getElementById(id);
    get("title").textContent = this._text("Srovnání statistik vody", "Water statistics comparison");
    get("period-label").textContent = this._text("Počet dní:", "Number of days:");
    get("start-label").textContent = this._text("Od (UTC, volitelné):", "From (UTC, optional):");
    get("end-label").textContent = this._text("Do (UTC, bez této hodiny):", "Until (UTC, exclusive):");
    get("resolution-label").textContent = this._text("Podrobnosti:", "Details:");
    for (const [value, cs, en] of [["hours", "Hodiny", "Hours"], ["days", "Dny", "Days"], ["months", "Měsíce", "Months"], ["years", "Roky", "Years"]]) {
      get("resolution").querySelector('option[value="' + value + '"]').textContent = this._text(cs, en);
    }
    get("refresh").textContent = this._text("Obnovit", "Refresh");
    get("export").textContent = this._text("Stáhnout anonymizovaný JSON", "Download pseudonymised JSON");
    get("period").value = String(this._days);
    get("period").addEventListener("change", () => {
      this._days = Number(get("period").value);
      get("start").value = get("end").value = "";
      this._load();
    });
    get("resolution").addEventListener("change", () => this._showRows());
    get("refresh").addEventListener("click", () => this._load());
    get("export").addEventListener("click", () => {
      if (this._result) downloadText(JSON.stringify(this._result, null, 2), "voda-porovnani.json", "application/json");
    });
  }
  async _load() {
    if (!this._hass || !this._config) return;
    this._render();
    const get = id => this.shadowRoot.getElementById(id);
    const serial = ++this._sequence;
    get("export").disabled = true;
    if (!this._hass.user?.is_admin) {
      get("status").textContent = this._text("Porovnání je dostupné pouze správci.", "Comparison is available to administrators only.");
      return;
    }
    get("status").textContent = this._text("Načítám pouze statistiky Recorderu…", "Reading Recorder statistics…");
    let end = Math.floor(Date.now() / 3600000) * 3600;
    let start = end - this._days * 86400;
    const fromInput = get("start").value;
    const toInput = get("end").value;
    if (fromInput || toInput) {
      start = Date.parse(fromInput + "Z") / 1000;
      end = Date.parse(toInput + "Z") / 1000;
      if (!fromInput || !toInput || !Number.isFinite(start) || !Number.isFinite(end) ||
          start % 3600 || end % 3600 || start >= end || end - start > 366 * 86400) {
        this._result = null;
        get("summary").replaceChildren();
        get("rows").replaceChildren();
        get("status").textContent = this._text(
          "Zadejte obě hranice jako celé hodiny UTC; rozsah nejvýše 366 dní.",
          "Enter both boundaries as whole UTC hours; the maximum range is 366 days.",
        );
        return;
      }
    }
    try {
      const result = await this._hass.callWS({
        type: DOMAIN + "/dashboard/compare", source_a: this._config.source_a,
        source_b: this._config.source_b, start, end,
      });
      if (serial !== this._sequence) return;
      // Výsledek obsahuje pseudonymy A/B, nikoli ID, adresy či autentizaci.
      this._result = result;
      get("export").disabled = false;
      get("status").textContent = this._text("Společné pokrytí: ", "Common coverage: ") +
        result.comparable_hours + "/" + result.requested_hours + this._text(" hodin.", " hours.");
      const summary = get("summary");
      summary.replaceChildren();
      const fmt = value => value === null ? "—" : Number(value).toLocaleString(undefined, {maximumFractionDigits: 6});
      for (const [label, value, unit] of [
        ["A · 79thales", result.a_m3, "m³"], ["B · rovocz", result.b_m3, "m³"],
        [this._text("Rozdíl A − B", "Difference A − B"), result.difference_litres, "l"],
      ]) {
        const box = addText(summary, "div", "");
        box.className = "box";
        addText(box, "strong", label);
        addText(box, "div", fmt(value) + " " + unit);
      }
      get("note").textContent = this._text(
        "Jen společné návazné hodiny; neznámé hodnoty nejsou nula. Původ kotev není ověřen. Nativní graf může zacházet s mezerami jinak.",
        "Only matching consecutive hours are compared; unknown values are not zero. Anchor provenance is unknown. Native charts may handle gaps differently.",
      );
      this._showRows();
    } catch {
      if (serial !== this._sequence) return;
      this._result = null;
      get("summary").replaceChildren();
      get("rows").replaceChildren();
      get("status").textContent = this._text("Statistiky nelze načíst; nejde o nulovou spotřebu.", "Statistics could not be read; this does not imply zero consumption.");
    }
  }
  _showRows() {
    if (!this._result) return;
    const get = id => this.shadowRoot.getElementById(id);
    const resolution = get("resolution").value;
    const heads = get("heads");
    heads.replaceChildren();
    for (const text of [this._text("Období", "Period"), "A m³", "B m³", "Δ l", this._text("Pokrytí / stav", "Coverage / status")]) addText(heads, "th", text);
    const rows = get("rows");
    rows.replaceChildren();
    const hourly = resolution === "hours";
    const notable = this._result[resolution].filter(row =>
      hourly ? !row.comparable || row.difference_m3 !== 0 : !row.complete || row.difference_m3 !== 0
    );
    const fmt = value => value === null ? "—" : Number(value).toLocaleString(undefined, {maximumFractionDigits: 6});
    for (const row of notable.slice(0, 200)) {
      const tr = document.createElement("tr");
      const label = hourly ? row.start_local : (row.date || row.period);
      const coverage = hourly ? row.status_a + " / " + row.status_b
        : row.comparable_hours + "/" + row.expected_hours + (row.complete ? "" : this._text(" (neúplné)", " (incomplete)"));
      for (const value of [label, fmt(row.a_m3), fmt(row.b_m3), fmt(row.difference_litres), coverage]) addText(tr, "td", value);
      rows.append(tr);
    }
    get("more").textContent = this._text("Odlišná či neúplná období: ", "Different or incomplete periods: ") +
      notable.length + this._text("; zobrazeno nejvýše 200. Export obsahuje celý rozsah a souhrny.", "; at most 200 shown. Export contains the full period and summaries.");
  }
}

const TEXT = {
  cs: {
    heading: "VSChrudim – generátor dashboardu",
    intro: "Náhled čte pouze registry a uložené statistiky. Portál, Energy ani odečty nemění.",
    entry: "Odběrné místo", title: "Název záložky", path: "Adresa pohledu v Přehledu", language: "Jazyk",
    candidate: "Porovnávací statistika rovocz (volitelné)", none: "Bez porovnání",
    same: "Potvrzuji, že zvolená statistika patří ke stejnému fyzickému vodoměru.",
    destination: "Výstup", yaml: "Jen náhled a YAML", append: "Přidat do horní lišty Přehledu",
    preview: "Připravit náhled", copy: "Kopírovat YAML", download: "Stáhnout YAML",
    consent: "Potvrzuji uvedený zápis do Lovelace a případnou registraci přibalené srovnávací karty.",
    save: "Provést potvrzený zápis", ready: "Náhled je připraven; nic nebylo uloženo.",
    saved: "Záložka byla uložena.", failed: "Akci se nepodařilo ověřit nebo dokončit. Existující data se nemažou. Při porovnání mohla zůstat zaregistrovaná místní karta.",
    noEntries: "Žádná načtená položka vodoměru není dostupná.", admin: "Generátor je dostupný pouze správci.",
    selectMeter: "Pro srovnání nejdříve potvrďte stejný vodoměr.", copyFailed: "YAML je označený; zkopírujte jej ručně.",
    copied: "YAML zkopírován.", resource: "Srovnávací karta je místní součást integrace. Pro ruční YAML přidejte tuto adresu jako JavaScript module:",
    warning: "Některé entity či statistiky nejsou dostupné; v náhledu se nevymýšlejí. Stav rovocz a jeho starší historie jsou nezávislé.",
    gap: "Při souběžné editaci Přehledu nelze mezi posledním čtením a uložením zaručit atomickou ochranu.",
    sourceState: "Stavy instalace rovocz:", open: "Otevřít dashboard",
  },
  en: {
    heading: "VSChrudim dashboard generator",
    intro: "Preview reads registries and stored statistics only; the portal, Energy and readings are unchanged.",
    entry: "Consumption place", title: "Tab title", path: "View path in Overview", language: "Language",
    candidate: "rovocz comparison statistic (optional)", none: "No comparison",
    same: "I confirm that the selected statistic belongs to the same physical meter.",
    destination: "Output", yaml: "Preview and YAML only", append: "Add to Overview top bar",
    preview: "Prepare preview", copy: "Copy YAML", download: "Download YAML",
    consent: "I confirm the Lovelace write and, if needed, registration of the bundled comparison card.",
    save: "Perform confirmed write", ready: "Preview is ready; nothing was saved.",
    saved: "Tab saved.", failed: "The operation could not be completed or verified. Existing data was not deleted. A comparison resource may already have been registered.",
    noEntries: "No loaded watermeter entry is available.", admin: "The generator is available to administrators only.",
    selectMeter: "Confirm the same meter before enabling comparison.", copyFailed: "YAML is selected; copy it manually.",
    copied: "YAML copied.", resource: "The comparison card is bundled locally. For manual YAML, add this URL as a JavaScript module:",
    warning: "Some entities or statistics are unavailable and were not invented. The rovocz installation state and its historical data are independent.",
    gap: "Atomic protection between the final read and save is unavailable when Overview is edited concurrently.",
    sourceState: "rovocz installation states:", open: "Open dashboard",
  },
};

class VsChrudimDashboardGenerator extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._busy = false;
    this._ready = false;
    this._revision = 0;
    this._hasSources = false;
  }
  set hass(value) {
    this._hass = value;
    if (!this._ready && value) { this._ready = true; this._render(); this._loadSources(); }
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.hass = value;
  }
  set narrow(value) { this._narrow = value; }
  _get(id) { return this.shadowRoot.getElementById(id); }
  _render() {
    const language = this._hass.language?.startsWith("en") ? "en" : "cs";
    this._text = TEXT[language];
    this.shadowRoot.innerHTML = [
      "<style>:host{display:block;color:var(--primary-text-color);background:var(--primary-background-color);min-height:100vh}",
      "header{height:56px;display:flex;align-items:center;padding:0 16px;background:var(--app-header-background-color)}",
      "main{max-width:1100px;margin:auto;padding:16px}.card{background:var(--card-background-color);border-radius:12px;padding:18px;margin-bottom:16px}",
      ".fields,.preview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}",
      "label{display:block}input:not([type=checkbox]),select,textarea{box-sizing:border-box;width:100%;padding:9px;font:inherit}",
      "textarea{min-height:220px;font-family:monospace}button{font:inherit;padding:9px;margin:6px 6px 6px 0}",
      ".check{display:flex;gap:8px;align-items:flex-start;margin:14px 0}p{color:var(--secondary-text-color)}[hidden]{display:none!important}</style>",
      '<header><ha-menu-button></ha-menu-button><strong data-text="heading"></strong></header>',
      '<main><div class="card"><p data-text="intro"></p><div class="fields">',
      '<label><span data-text="entry"></span><select id="entry"></select></label>',
      '<label><span data-text="title"></span><input id="title" maxlength="100" value="Voda VSChrudim"></label>',
      '<label><span data-text="path"></span><input id="path" maxlength="60" value="voda-vschrudim"></label>',
      '<label><span data-text="language"></span><select id="language"><option value="cs">Čeština</option><option value="en">English</option></select></label>',
      '<label><span data-text="candidate"></span><select id="candidate"><option value=""></option></select></label>',
      '<label><span data-text="destination"></span><select id="destination"><option value="append"></option><option value="yaml"></option></select></label>',
      '</div><p id="source-state"></p><label class="check" id="same-box" hidden><input type="checkbox" id="same"><span data-text="same"></span></label>',
      '<p data-text="gap"></p><button id="preview" data-text="preview" disabled></button></div>',
      '<div class="card" id="output" hidden><p id="warning" hidden data-text="warning"></p><p id="resource" hidden></p>',
      '<div class="preview-grid" id="visual-preview"></div><textarea id="yaml-output" readonly spellcheck="false"></textarea>',
      '<div><button id="copy" data-text="copy"></button><button id="download" data-text="download"></button></div>',
      '<label class="check" id="consent-box" hidden><input type="checkbox" id="consent"><span data-text="consent"></span></label>',
      '<button id="save" data-text="save" hidden disabled></button></div>',
      '<div class="card"><p id="status" role="status" aria-live="polite"></p><a id="open" hidden data-text="open"></a></div></main>',
    ].join("");
    this.shadowRoot.querySelectorAll("[data-text]").forEach(node => { node.textContent = this._text[node.dataset.text]; });
    const get = id => this._get(id);
    get("candidate").options[0].textContent = this._text.none;
    for (const key of ["yaml", "append"]) {
      get("destination").querySelector('option[value="' + key + '"]').textContent = this._text[key];
    }
    get("language").value = language;
    for (const id of ["entry", "title", "path", "language", "candidate", "same", "destination"]) {
      get(id).addEventListener("change", () => this._invalidate());
      if (id === "title" || id === "path") get(id).addEventListener("input", () => this._invalidate());
    }
    get("candidate").addEventListener("change", () => {
      get("same-box").hidden = !get("candidate").value;
      get("same").checked = false;
    });
    get("consent").addEventListener("change", () => this._sync());
    get("preview").addEventListener("click", () => this._generate());
    get("copy").addEventListener("click", () => this._copy());
    get("download").addEventListener("click", () => {
      if (this._preview) downloadText(this._preview.yaml, this._preview.url_path + ".yaml", "application/yaml;charset=utf-8");
    });
    get("save").addEventListener("click", () => this._save());
  }
  _status(message, path = "") {
    this._get("status").textContent = message;
    this._get("open").hidden = !path;
    if (path) this._get("open").href = path;
  }
  _invalidate() {
    this._revision++;
    this._preview = null;
    this._get("output").hidden = true;
    this._get("consent").checked = false;
    this._sync();
  }
  _sync() {
    const writing = this._get("destination").value !== "yaml";
    this._get("consent-box").hidden = !writing;
    this._get("save").hidden = !writing;
    this._get("save").disabled = this._busy || !this._preview || !this._get("consent").checked;
  }
  _setBusy(value) {
    // Změna vstupu během čekání by mohla zpřístupnit neaktuální náhled.
    this._busy = value;
    for (const id of ["entry", "title", "path", "language", "candidate", "same", "destination"]) {
      this._get(id).disabled = value;
    }
    this._get("preview").disabled = value || !this._hasSources;
    this._sync();
  }
  async _loadSources() {
    if (!this._hass.user?.is_admin) { this._status(this._text.admin); return; }
    try {
      const data = await this._hass.callWS({type: DOMAIN + "/dashboard/sources"});
      for (const entry of data.entries) {
        const option = document.createElement("option");
        option.value = entry.entry_id;
        option.textContent = entry.name;
        this._get("entry").append(option);
      }
      for (const candidate of data.candidates) {
        const option = document.createElement("option");
        option.value = candidate.statistic_id;
        option.textContent = candidate.name + " (" + candidate.statistic_id + ", " + candidate.unit + ")";
        this._get("candidate").append(option);
      }
      const selected = new URLSearchParams(window.location.search).get("entry_id");
      if (data.entries.some(entry => entry.entry_id === selected)) this._get("entry").value = selected;
      this._get("source-state").textContent = this._text.sourceState + " " + (data.comparison_integration_states.join(", ") || "—");
      if (!data.entries.length) { this._status(this._text.noEntries); return; }
      this._hasSources = true;
      this._get("preview").disabled = false;
    } catch { this._status(this._text.failed); }
  }
  async _generate() {
    if (this._busy) return;
    if (this._get("candidate").value && !this._get("same").checked) {
      this._status(this._text.selectMeter);
      return;
    }
    this._invalidate();
    const revision = this._revision;
    this._setBusy(true);
    const request = {
      type: DOMAIN + "/dashboard/preview", entry_id: this._get("entry").value,
      title: this._get("title").value.trim(), url_path: this._get("path").value.trim(),
      language: this._get("language").value,
      target: "overview",
      same_meter: this._get("same").checked,
    };
    if (this._get("candidate").value) request.comparison = this._get("candidate").value;
    try {
      const result = await this._hass.callWS(request);
      if (revision !== this._revision) return;
      this._preview = {...result, title: request.title, url_path: request.url_path};
      this._get("yaml-output").value = result.yaml;
      this._get("warning").hidden = !result.missing_entities.length && !result.missing_statistics.length;
      this._get("resource").hidden = !result.resource_url;
      if (result.resource_url) this._get("resource").textContent = this._text.resource + " " + result.resource_url;
      this._get("output").hidden = false;
      this._status(this._text.ready);
      await this._visualPreview(result.config.views);
    } catch { this._status(this._text.failed); }
    finally { this._setBusy(false); }
  }
  async _visualPreview(views) {
    // Helper vykreslí hlavní i detailní pohled. Inertní náhled zabrání
    // neúmyslnému stisku akcí; při chybě nadále zůstane kompletní YAML.
    const host = this._get("visual-preview");
    host.replaceChildren();
    host.setAttribute("inert", "");
    if (typeof window.loadCardHelpers !== "function") return;
    try {
      const helpers = await window.loadCardHelpers();
      for (const view of views) {
        const viewHost = document.createElement("div");
        host.append(viewHost);
        addText(viewHost, "h3", view.title);
        for (const section of view.sections) {
          const column = document.createElement("div");
          viewHost.append(column);
          for (const config of section.cards) {
            const card = await helpers.createCardElement(config);
            card.hass = this._hass;
            column.append(card);
          }
        }
      }
    } catch { host.replaceChildren(); }
  }
  async _copy() {
    try { await navigator.clipboard.writeText(this._get("yaml-output").value); this._status(this._text.copied); }
    catch { this._get("yaml-output").focus(); this._get("yaml-output").select(); this._status(this._text.copyFailed); }
  }
  async _save() {
    if (this._busy || !this._preview || !this._get("consent").checked) return;
    const destination = this._get("destination").value;
    if (destination === "yaml") return;
    this._setBusy(true);
    try {
      const callWS = message => this._hass.callWS(message);
      const result = await appendViews(callWS, this._preview);
      const path = "/lovelace/" + result.view_path;
      this._status(this._text.saved, path);
    } catch (error) {
      const path = error.url_path ? "/" + error.url_path : "";
      this._status(this._text.failed + " (" + (error.message || "unknown") + ")", path);
    } finally {
      this._get("consent").checked = false;
      this._setBusy(false);
    }
  }
}

if (!customElements.get("vschrudim-statistics-comparison")) {
  customElements.define("vschrudim-statistics-comparison", VsChrudimComparisonCard);
}
if (!customElements.get("vschrudim-dashboard-generator")) {
  customElements.define("vschrudim-dashboard-generator", VsChrudimDashboardGenerator);
}

export {sameConfig, appendViews, ensureCompareResource};
