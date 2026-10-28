/* Reports: Insights (figures and charts), the report builder (on screen, Excel, CSV, saved reports) and the activity log. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  // Chart colours: the validated categorical slots 1 and 2 (blue, orange) - adjacent CVD ΔE 24.7, contrast ≥ 3:1 on white.
  const SERIES = ["#2a78d6", "#eb6834"];
  const INK = {grid: "#e9edf3", base: "#c9d2de"};
  let config = null, catalog = null;
  const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const insight = {entity: "", months: "6"};
  const builder = {dataset: "envelopes", columns: null, range: "90", since: "", until: "", entity: "", status: "", owner: "", client: "", page: 0};
  const log = {range: "30", since: "", until: "", user: "", category: "", client: "", q: "", page: 0, rows: []};

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function button(text, fn, cls = "button button-quiet") { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", () => Promise.resolve().then(fn).catch(fail)); return b; }
  function fail(e) { if (!e || !e.message) return; const box = $("app-error"); box.textContent = e.message; box.hidden = false; box.scrollIntoView({behavior: "smooth", block: "center"}); }
  function toast(text) { let t = document.querySelector(".toast"); if (!t) { t = el("div", "toast"); t.setAttribute("role", "status"); document.body.append(t); } t.textContent = text; t.hidden = false; setTimeout(() => { t.hidden = true; }, 4000); }
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  async function api(path, body, raw = false) {
    const r = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: body === undefined ? {} : {"Content-Type": "application/json", "X-CSRF-Token": csrf()},
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
    if (r.status === 401) { location.href = "/login"; throw new Error("Please sign in again."); }
    if (raw && r.ok) return r;
    let d = {}; try { d = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw new Error(d.error || `The request failed (${r.status}).`);
    return d;
  }
  function field(label, control) { const wrap = el("div", "field"); const l = el("label", "", label); control.id = control.id || `rp-${Math.random().toString(36).slice(2)}`; l.htmlFor = control.id; wrap.append(l, control); return wrap; }
  function select(options, value) { const s = el("select"); options.forEach(([v, t]) => s.append(new Option(t, v))); s.value = value ?? options[0]?.[0] ?? ""; return s; }
  function input(value = "", type = "text", attrs = {}) { const i = el("input"); i.type = type; i.value = value ?? ""; Object.entries(attrs).forEach(([k, v]) => { i[k] = v; }); return i; }
  const fmt = n => n === null || n === undefined ? "—" : Number(n).toLocaleString();
  async function download(path, body) {
    const r = await api(path, body, true);
    const blob = await r.blob();
    const name = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") || "")?.[1] || "report";
    const a = el("a"); a.href = URL.createObjectURL(blob); a.download = name; document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast(`Downloaded ${name}`);
  }
  const RANGES = [["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last 12 months"], ["ytd", "This year"], ["all", "All time"], ["custom", "Custom range"]];
  function rangeOf(state) {
    const now = Date.now() / 1000;
    if (state.range === "all") return [null, null];
    if (state.range === "ytd") return [new Date(new Date().getFullYear(), 0, 1).getTime() / 1000, null];
    if (state.range === "custom") return [state.since ? new Date(`${state.since}T00:00:00`).getTime() / 1000 : null, state.until ? new Date(`${state.until}T00:00:00`).getTime() / 1000 + 86400 : null];
    return [Math.floor(now - Number(state.range) * 86400), null];
  }
  function rangeControls(state, onChange) {
    const wrap = el("div", "filter-grid");
    const preset = select(RANGES, state.range);
    const from = input(state.since, "date"), to = input(state.until, "date");
    const custom = el("div", "settings-grid"); custom.append(field("From", from), field("To", to)); custom.hidden = state.range !== "custom";
    preset.addEventListener("change", () => { state.range = preset.value; custom.hidden = preset.value !== "custom"; if (preset.value !== "custom") onChange(); });
    from.addEventListener("change", () => { state.since = from.value; onChange(); }); to.addEventListener("change", () => { state.until = to.value; onChange(); });
    wrap.append(field("Date range", preset), custom);
    return wrap;
  }
  function tabs(active) {
    const t = el("nav", "tabs"); t.setAttribute("aria-label", "Reports");
    [["insights", "Insights", "#/reports/insights"], ["builder", "Report builder", "#/reports/builder"], ...(config.user.role !== "user" ? [["activity", "Activity log", "#/activity"]] : [])]
      .forEach(([key, label, href]) => { const a = el("a", "", label); a.href = href; if (key === active) a.setAttribute("aria-current", "page"); t.append(a); });
    return t;
  }

  // ------------------------------------------------------------------ charts (SVG, hover tooltips, data table)
  function niceMax(v) { if (v <= 4) return 4; const p = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p; return 10 * p; }
  function svg(tag, attrs = {}) { const n = document.createElementNS("http://www.w3.org/2000/svg", tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); return n; }
  // A column with a 4px rounded data end and a square base.
  function columnPath(x, y, w, h, r = 4) { r = Math.min(r, w / 2, h); return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`; }
  function tooltip(container) { const t = el("div", "chart-tooltip"); t.hidden = true; container.append(t); return t; }
  function showTip(tip, x, y, title, rows) {
    tip.replaceChildren(el("div", "", title));
    rows.forEach(([name, value, color]) => { const line = el("div"); const key = el("span"); key.style.cssText = `display:inline-block;width:12px;height:2px;background:${color};margin:0 6px 3px 0;vertical-align:middle`; const b = el("b", "", value); line.append(key, b, document.createTextNode(` ${name}`)); tip.append(line); });
    tip.style.left = `${x}px`; tip.style.top = `${y}px`; tip.hidden = false;
  }
  function legend(series) { const l = el("div", "chart-legend"); series.forEach(s => { const item = el("span"); const sw = el("i"); sw.style.background = s.color; item.append(sw, document.createTextNode(s.name)); l.append(item); }); return l; }
  function dataTable(head, rows) {
    const d = el("details"); d.style.marginTop = "10px"; d.append(el("summary", "muted small", "Show the data as a table"));
    const t = el("table", "portal-table"); t.style.minWidth = "0"; const tr = el("tr"); head.forEach(h => tr.append(el("th", "", h))); const th = el("thead"); th.append(tr); t.append(th);
    const tb = el("tbody"); rows.forEach(r => { const row = el("tr"); r.forEach(v => row.append(el("td", "", v))); tb.append(row); }); t.append(tb); d.append(t); return d;
  }
  function columnChart(categories, series, height = 230) {
    const wrap = el("div", "chart");
    if (series.length > 1) wrap.append(legend(series));
    const W = 640, H = height, pad = {l: 34, r: 8, t: 10, b: 26};
    const max = niceMax(Math.max(1, ...series.flatMap(s => s.values)));
    const s = svg("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": series.map(x => x.name).join(" and ") + " by month"});
    const plotH = H - pad.t - pad.b, plotW = W - pad.l - pad.r, band = plotW / categories.length;
    const grid = svg("g", {class: "grid"}), axis = svg("g", {class: "axis"});
    for (let i = 0; i <= 4; i++) {
      const v = max / 4 * i, y = pad.t + plotH - plotH * i / 4;
      if (i) grid.append(svg("line", {x1: pad.l, x2: W - pad.r, y1: y, y2: y}));
      const t = svg("text", {x: pad.l - 8, y: y + 4, "text-anchor": "end"}); t.textContent = v >= 1000 ? `${v / 1000}K` : String(Math.round(v * 10) / 10); axis.append(t);
    }
    s.append(grid, svg("line", {class: "baseline", x1: pad.l, x2: W - pad.r, y1: pad.t + plotH, y2: pad.t + plotH}));
    const barW = Math.min(24, (band * 0.62 - (series.length - 1) * 2) / series.length);
    const tip = tooltip(wrap);
    categories.forEach((cat, ci) => {
      const groupW = barW * series.length + (series.length - 1) * 2, x0 = pad.l + band * ci + (band - groupW) / 2;
      series.forEach((ser, si) => {
        const v = ser.values[ci], h = plotH * v / max;
        if (h > 0) s.append(svg("path", {d: columnPath(x0 + si * (barW + 2), pad.t + plotH - h, barW, h), fill: ser.color}));
      });
      const label = svg("text", {x: pad.l + band * ci + band / 2, y: H - 8, "text-anchor": "middle"}); label.textContent = cat; axis.append(label);
      // The whole band is the hit target; it shows every series for that month.
      const hit = svg("rect", {x: pad.l + band * ci, y: pad.t, width: band, height: plotH, fill: "transparent", tabindex: "0", "aria-label": `${cat}: ${series.map(x => `${x.name} ${x.values[ci]}`).join(", ")}`});
      const show = () => { const r = s.getBoundingClientRect(); showTip(tip, (pad.l + band * ci + band / 2) / W * r.width, pad.t / H * r.height + 6, cat, series.map(x => [x.name, fmt(x.values[ci]), x.color])); hit.setAttribute("fill", "rgba(31,85,214,.05)"); };
      const hide = () => { tip.hidden = true; hit.setAttribute("fill", "transparent"); };
      hit.addEventListener("pointerenter", show); hit.addEventListener("focus", show); hit.addEventListener("pointerleave", hide); hit.addEventListener("blur", hide);
      s.append(hit);
    });
    s.append(axis);
    wrap.append(s);
    wrap.append(dataTable(["Month", ...series.map(x => x.name)], categories.map((c, i) => [c, ...series.map(x => fmt(x.values[i]))])));
    return wrap;
  }
  function barList(rows, color = SERIES[0], format = fmt, onClick) {
    const wrap = el("div", "bar-list");
    const max = Math.max(1, ...rows.map(r => r.value));
    rows.forEach(r => {
      const row = el(onClick ? "button" : "div", "bar-row"); if (onClick) { row.type = "button"; row.style.cssText = "border:0;background:none;padding:0;text-align:left;cursor:pointer"; row.addEventListener("click", () => onClick(r)); }
      const track = el("span", "track"); const fill = el("i"); fill.style.width = `${r.value / max * 100}%`; fill.style.background = color; track.append(fill);
      row.title = `${r.label}: ${format(r.value)}`;
      row.append(el("span", "label", r.label), track, el("span", "value", format(r.value)));
      wrap.append(row);
    });
    return wrap;
  }

  // ------------------------------------------------------------------ Insights
  async function showInsights() {
    window.portalMark?.("#/reports/insights");
    const view = $("reports-view"); view.replaceChildren();
    const head = el("div", "page-head"); const left = el("div");
    left.append(el("p", "eyebrow", "Reports"), el("h1", "", "Insights"), el("p", "page-sub", "How your agreements are moving: what was sent, what is completed, how long signing takes and what is still waiting."));
    head.append(left); view.append(head, tabs("insights"));
    const filters = el("div", "list-toolbar");
    const company = select([["", "All companies"], ...config.entities.map(e => [e.name, e.short || e.name])], insight.entity); company.setAttribute("aria-label", "Company");
    const months = select([["3", "Last 3 months"], ["6", "Last 6 months"], ["12", "Last 12 months"], ["24", "Last 24 months"]], insight.months); months.setAttribute("aria-label", "Period");
    const reload = () => { insight.entity = company.value; insight.months = months.value; showInsights().catch(fail); };
    company.addEventListener("change", reload); months.addEventListener("change", reload);
    if (config.entities.length > 1) filters.append(company);
    filters.append(months);
    view.append(filters);
    const d = await api(`/api/insights?entity=${encodeURIComponent(insight.entity)}&months=${insight.months}&tz=${encodeURIComponent(TZ)}`);
    const t = d.totals;
    const kpis = el("div", "kpi-row kpi-6");
    [["Sent", fmt(t.sent), "#/agreements/sent"], ["Completed", fmt(t.completed), "#/agreements/completed"], ["Completion rate", t.completion_rate === null ? "—" : `${t.completion_rate}%`, null],
     ["Average time to complete", t.avg_days === null ? "—" : t.avg_days < 1 ? (t.avg_days * 24 < 1 ? "Under 1 hour" : `${Math.round(t.avg_days * 24)} hours`) : `${t.avg_days} days`, null], ["Waiting now", fmt(t.waiting), "#/agreements/waiting"], ["Agreements generated", fmt(t.generated), "#/clients"]]
      .forEach(([label, value, href]) => { const k = el(href ? "a" : "div", "kpi"); if (href) k.href = href; k.append(el("span", "", label), el("strong", "", value)); kpis.append(k); });
    view.append(kpis);
    const grid = el("div", "chart-grid");
    const card = (title, sub) => { const c = el("section", "chart-card"); c.append(el("h3", "", title), el("p", "chart-sub", sub)); grid.append(c); return c; };
    const labels = d.months.map(m => m.label.split(" ")[0]);
    card("Sent and completed", "Envelopes sent, and envelopes completed, each month.")
      .append(columnChart(labels, [{name: "Sent", color: SERIES[0], values: d.months.map(m => m.sent)}, {name: "Completed", color: SERIES[1], values: d.months.map(m => m.completed)}]));
    card("Agreements generated", "BAA, Services Agreement and NDA packages created each month.")
      .append(columnChart(labels, [{name: "Generated", color: SERIES[0], values: d.months.map(m => m.generated)}]));
    const waiting = card("Waiting for signature, by age", `${fmt(t.waiting)} envelope${t.waiting === 1 ? "" : "s"} waiting, by days since they were sent.`);
    waiting.append(barList(d.pending_age.map(x => ({label: x.bucket, value: x.count}))));
    const funnel = card("From sent to signed", "Signers of envelopes sent in the period.");
    const f = el("div", "funnel");
    [["Received", d.funnel.sent], ["Viewed", d.funnel.viewed], ["Signed", d.funnel.signed]].forEach(([label, value]) => { const box = el("div"); box.append(el("strong", "", fmt(value)), el("span", "", label + (d.funnel.sent && label !== "Received" ? ` · ${Math.round(value * 100 / d.funnel.sent)}%` : ""))); f.append(box); });
    funnel.append(f);
    if (d.by_company.length > 1) {
      const by = card("By company", "Sent and completed in the period.");
      by.append(legend([{name: "Sent", color: SERIES[0]}, {name: "Completed", color: SERIES[1]}]));
      const max = Math.max(1, ...d.by_company.map(x => x.sent));
      d.by_company.forEach(x => {
        const block = el("div"); block.style.margin = "0 0 12px"; block.append(el("div", "small", x.short));
        [["Sent", x.sent, SERIES[0]], ["Completed", x.completed, SERIES[1]]].forEach(([label, value, color]) => {
          const row = el("div", "bar-row"); row.style.gridTemplateColumns = "90px minmax(0,1fr) 44px";
          const track = el("span", "track"); const fill = el("i"); fill.style.width = `${value / max * 100}%`; fill.style.background = color; track.append(fill);
          row.append(el("span", "label muted", label), track, el("span", "value", fmt(value))); block.append(row);
        });
        by.append(block);
      });
    }
    const top = card("Top clients", "Most envelopes sent in the period.");
    if (d.top_clients.length) top.append(barList(d.top_clients.map(x => ({label: x.client, value: x.sent, id: x.client_id})), SERIES[0], fmt, r => window.portalRoute(`#/clients/${r.id}`)));
    else top.append(el("p", "muted small", "No envelopes sent in this period."));
    view.append(grid);
  }

  // ------------------------------------------------------------------ report builder
  function spec() { return catalog.datasets.find(x => x.key === builder.dataset) || catalog.datasets[0]; }
  function payload(extra = {}) {
    const [since, until] = rangeOf(builder);
    return {dataset: builder.dataset, columns: builder.columns || spec().default, filters: {since, until, entity: builder.entity, status: builder.status, owner: builder.owner, client: builder.client, tz: TZ}, ...extra};
  }
  async function showBuilder() {
    window.portalMark?.("#/reports/builder");
    catalog = catalog || await api("/api/reports/catalog");
    const view = $("reports-view"); view.replaceChildren();
    const head = el("div", "page-head"); const left = el("div");
    left.append(el("p", "eyebrow", "Reports"), el("h1", "", "Report builder"), el("p", "page-sub", "Choose what to report on, the columns and the filters. See it on screen, or download it for Excel."));
    head.append(left); view.append(head, tabs("builder"));
    const layout = el("div", "report-layout");
    const config_ = el("aside", "report-config");
    const what = el("section", "card"); what.append(el("h3", "", "Report"));
    const sets = el("div", "dataset-list");
    const NOTES = {envelopes: "One row per envelope", recipients: "One row per recipient: who viewed and signed", agreements: "Generated BAA, Services and NDA packages",
      clients: "Clients with their agreements and signatures", activity: "Who did what, when, and from where", users: "Accounts, roles and their work"};
    catalog.datasets.forEach(ds => {
      const b = el("button"); b.type = "button"; b.setAttribute("aria-pressed", String(ds.key === builder.dataset)); b.append(document.createTextNode(ds.label), el("small", "", NOTES[ds.key] || ""));
      b.addEventListener("click", () => { builder.dataset = ds.key; builder.columns = null; builder.page = 0; showBuilder().catch(fail); });
      sets.append(b);
    });
    what.append(sets); config_.append(what);
    const cols = el("section", "card"); cols.append(el("h3", "", "Columns"));
    const list = el("div", "column-list");
    const chosen = new Set(builder.columns || spec().default);
    spec().columns.forEach(c => {
      const l = el("label"); const box = input("", "checkbox", {checked: chosen.has(c.key)});
      box.addEventListener("change", () => { if (box.checked) chosen.add(c.key); else chosen.delete(c.key); builder.columns = spec().columns.map(x => x.key).filter(k => chosen.has(k)); });
      l.append(box, document.createTextNode(c.label)); list.append(l);
    });
    cols.append(list); config_.append(cols);
    const filt = el("section", "card"); filt.append(el("h3", "", "Filters"));
    const grid = el("div", "filter-grid");
    grid.append(rangeControls(builder, () => {}));
    grid.lastChild.querySelector("label").textContent = `Date range · ${spec().date.toLowerCase()}`;
    if (catalog.companies.length > 1) { const c = select([["", "All companies"], ...catalog.companies.map(x => [x.name, x.short])], builder.entity); c.addEventListener("change", () => { builder.entity = c.value; }); grid.append(field("Company", c)); }
    if (["envelopes", "recipients", "agreement_log"].includes(builder.dataset)) { const st = select([["", "Any status"], ...catalog.statuses.map(x => [x.key, x.label])], builder.status); st.addEventListener("change", () => { builder.status = st.value; }); grid.append(field("Status", st)); }
    if (catalog.users.length > 1 && builder.dataset !== "clients") { const u = select([["", "Everyone"], ...catalog.users.map(x => [x.id, x.name])], builder.owner); u.addEventListener("change", () => { builder.owner = u.value; }); grid.append(field(builder.dataset === "activity" ? "User" : builder.dataset === "users" ? "User" : "Sender", u)); }
    if (builder.dataset !== "users") { const cl = input(builder.client, "search", {placeholder: "Name contains"}); cl.addEventListener("input", () => { builder.client = cl.value; }); grid.append(field("Client", cl)); }
    filt.append(grid);
    const run = button("Run report", () => { builder.page = 0; return results(); }, "button button-primary"); run.style.width = "100%"; run.style.marginTop = "12px";
    filt.append(run); config_.append(filt);
    const saved = el("section", "card"); saved.append(el("h3", "", "Saved reports"));
    if (!catalog.saved.length) saved.append(el("p", "muted small", "Save a report to run it again in one click."));
    catalog.saved.forEach(r => {
      const row = el("div", "library-item"); row.style.gridTemplateColumns = "minmax(0,1fr) auto auto";
      row.append(el("strong", "", r.name), button("Open", () => { Object.assign(builder, {dataset: r.definition.dataset, columns: r.definition.columns?.length ? r.definition.columns : null, page: 0}, r.definition.filters || {}); return showBuilder().then(results); }, "button button-quiet button-sm"),
        button("×", async () => { if (!confirm(`Delete the saved report ${r.name}?`)) return; await api(`/api/reports/saved/${r.id}/delete`, {}); catalog = null; await showBuilder(); }, "icon-button"));
      saved.append(row);
    });
    saved.append(button("Save this report", async () => {
      const name = prompt("Name this report", spec().label);
      if (!name) return;
      await api("/api/reports/saved", {name, definition: {dataset: builder.dataset, columns: builder.columns || spec().default, filters: {entity: builder.entity, status: builder.status, owner: builder.owner, client: builder.client, range: builder.range}}});
      catalog = null; toast("Report saved."); await showBuilder();
    }, "button button-ghost"));
    config_.append(saved);
    const main = el("div"); main.id = "report-results";
    layout.append(config_, main);
    view.append(layout);
    await results();
  }
  async function results() {
    const main = $("report-results"); if (!main) return;
    main.style.opacity = ".6";
    const d = await api("/api/reports/run", payload({page: builder.page}));
    main.style.opacity = "";
    main.replaceChildren();
    const head = el("div", "result-head"); const title = el("div");
    title.append(el("h2", "", spec().label), el("p", "muted small", `${fmt(d.total)}${d.capped ? "+" : ""} row${d.total === 1 ? "" : "s"}${d.capped ? " (showing the first 10,000; the Excel download has up to 50,000)" : ""}`));
    const actions = el("div", "page-actions");
    actions.append(button("Download Excel", () => download("/api/reports/export", payload({format: "xlsx"})), "button button-primary"), button("Download CSV", () => download("/api/reports/export", payload({format: "csv"}))));
    head.append(title, actions); main.append(head);
    if (!d.rows.length) { const e = el("div", "empty-state"); e.append(el("strong", "", "No rows match"), el("p", "", "Widen the date range or clear a filter.")); main.append(e); return; }
    const card = el("div", "panel table-card"); const body = el("div", "panel-body");
    const t = el("table", "portal-table"); const tr = el("tr"); d.columns.forEach(c => tr.append(el("th", "", c.label))); const th = el("thead"); th.append(tr); t.append(th);
    const tb = el("tbody");
    d.rows.forEach((r, i) => {
      const row = el("tr"); r.forEach((v, j) => { const td = el("td", d.columns[j].type === "number" ? "num" : "", v); if (d.columns[j].type === "number") td.style.textAlign = "right"; row.append(td); });
      const target = d.links[i];
      if (target?.envelope || target?.client) { row.style.cursor = "pointer"; row.tabIndex = 0; const go = () => window.portalRoute(target.envelope ? `#/envelope/${target.envelope}` : `#/clients/${target.client}`); row.addEventListener("click", go); row.addEventListener("keydown", e => { if (e.key === "Enter") go(); }); }
      tb.append(row);
    });
    t.append(tb); body.append(t); card.append(body); main.append(card);
    const paging = el("div", "portal-paging");
    const back = button("Previous", () => { builder.page--; return results(); }), more = button("Next", () => { builder.page++; return results(); });
    back.disabled = builder.page === 0; more.disabled = !d.has_more;
    paging.append(back, el("span", "", `Page ${builder.page + 1} of ${Math.max(1, Math.ceil(d.total / 100))}`), more);
    main.append(paging, el("p", "data-table-note", `Times are in your time zone (${TZ}). Excel files include a “Report details” sheet with the filters used.`));
  }

  // ------------------------------------------------------------------ activity log (Admin and above)
  const CATEGORIES = [["", "All activity"], ["access", "Sign-in and accounts"], ["clients", "Clients"], ["agreements", "Agreements (generate, download)"], ["envelopes", "Envelopes and signers"],
    ["settings", "Settings"], ["exports", "Reports and exports"]];
  function activityQuery(page) {
    const [since, until] = rangeOf(log);
    const p = new URLSearchParams({page, since: since || "", until: until || "", user: log.user, category: log.category, client: log.client, q: log.q, tz: TZ});
    return p;
  }
  async function showActivity() {
    window.portalMark?.("#/activity");
    catalog = catalog || await api("/api/reports/catalog");
    const view = $("activity-view"); view.replaceChildren();
    const head = el("div", "page-head"); const left = el("div");
    left.append(el("p", "eyebrow", "Admin"), el("h1", "", "Activity log"), el("p", "page-sub", "Who did what and when: sign-ins, clients opened, agreements generated and downloaded, envelopes sent, viewed and signed, files downloaded, reports exported. Each entry shows the IP address and browser."));
    const actions = el("div", "page-actions");
    const exportFilters = () => { const [since, until] = rangeOf(log); return {since, until, user: log.user, category: log.category, client: log.client, q: log.q, tz: TZ}; };
    actions.append(button("Download Excel", () => download("/api/activity/export", {format: "xlsx", filters: exportFilters()}), "button button-primary"), button("Download CSV", () => download("/api/activity/export", {format: "csv", filters: exportFilters()})));
    head.append(left, actions); view.append(head);
    const filters = el("div", "activity-filters");
    const preset = select(RANGES.filter(r => r[0] !== "custom"), log.range); preset.addEventListener("change", () => { log.range = preset.value; reload(); });
    const who = select([["", "Everyone"], ["signers", "Signers (no account)"], ...catalog.users.map(u => [u.id, u.name])], log.user); who.addEventListener("change", () => { log.user = who.value; reload(); });
    const category = select(CATEGORIES, log.category); category.addEventListener("change", () => { log.category = category.value; reload(); });
    const client = input(log.client, "search", {placeholder: "Client name"}); client.addEventListener("change", () => { log.client = client.value.trim(); reload(); });
    const q = input(log.q, "search", {placeholder: "Search name or action"}); q.addEventListener("change", () => { log.q = q.value.trim(); reload(); });
    filters.append(field("When", preset), field("Who", who), field("What", category), field("Client", client), field("Search", q), button("Apply", () => reload(), "button button-quiet"));
    view.append(filters);
    const card = el("div", "panel table-card"); const body = el("div", "panel-body");
    const t = el("table", "portal-table"); const tr = el("tr"); ["Time", "Who", "Action", "Details", "Client", "IP address", "Browser"].forEach(h => tr.append(el("th", "", h)));
    const th = el("thead"); th.append(tr); t.append(th); const tb = el("tbody"); tb.id = "activity-rows"; t.append(tb);
    body.append(t); card.append(body); view.append(card);
    const more = button("Load more", () => page(log.page + 1)); more.id = "activity-more"; more.style.marginTop = "14px";
    view.append(more, el("p", "data-table-note", `Times are in your time zone (${TZ}). Signers' entries come from each envelope's tamper-evident audit trail.`));
    const reload = () => { log.page = 0; $("activity-rows").replaceChildren(); page(0).catch(fail); };
    await page(0);
  }
  async function page(n) {
    const d = await api(`/api/activity?${activityQuery(n)}`);
    log.page = n;
    const tb = $("activity-rows"); if (!tb) return;
    if (!d.entries.length && !n) { const tr = el("tr"); const td = el("td", "", "No activity matches these filters."); td.colSpan = 7; td.style.textAlign = "center"; td.style.color = "var(--ink-3)"; tr.append(td); tb.append(tr); }
    d.entries.forEach(e => {
      const tr = el("tr");
      const time = el("td", "when-cell", new Date(e.at * 1000).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}));
      const who = el("td"); const w = el("div", "who-cell"); w.append(el("span", `type-dot ${e.type}`), el("span", "", e.who)); who.append(w);
      if (e.type !== "user") who.append(el("small", "", e.type === "signer" ? "Signer" : "System"));
      const action = el("td"); action.append(el("strong", "", e.action));
      const detail = el("td", "", e.detail);
      const client = el("td");
      if (e.client_id && e.client) { const a = el("a", "", e.client); a.href = `#/clients/${e.client_id}`; client.append(a); }
      if (e.envelope_id) { const a = el("a", "small", "Open envelope"); a.href = `#/envelope/${e.envelope_id}`; a.style.display = "block"; client.append(a); }
      tr.append(time, who, action, detail, client, el("td", "", e.ip), el("td", "", e.browser));
      tb.append(tr);
    });
    const more = $("activity-more"); if (more) more.hidden = !d.has_more;
  }

  window.reportsShow = tab => (tab === "builder" ? showBuilder() : showInsights()).catch(fail);
  window.activityShow = () => showActivity().catch(fail);
  window.reportsReady = c => { config = c; catalog = null; };
})();
