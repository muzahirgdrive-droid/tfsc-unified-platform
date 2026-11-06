/* Client records: the clients register (#/clients), the client record's agreements and "Signed BAA by company", "Add a past
   signed agreement", "Import previous clients" (#/clients/import) and the Signed BAA register (#/clients/baa-register).
   Used by clients.js through window.Records. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const SOURCES = {portal: "Portal", docusign: "DocuSign", manual: "Manual (email)", jotform: "Jotform", other: "Other"};
  const KINDS = {baa: "Business Associate Agreement", services: "Services Agreement", nda: "Mutual NDA", other: "Other agreement"};
  const SHORT_KINDS = {baa: "BAA", services: "Services", nda: "NDA", other: "Other"};
  const STATUS = {declined: "Declined", voided: "Voided", expired: "Expired", completing: "Finishing"};
  const REASONS = {"name in file": "name in file", "file name": "file name", envelope: "envelope", "client number": "client number", spreadsheet: "spreadsheet",
    name: "spreadsheet", "your choice": "your choice", "same name": "same name"};

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function fail(e) { if (!e || !e.message) return; const box = $("app-error"); box.textContent = e.message; box.hidden = false; box.scrollIntoView({behavior: "smooth", block: "center"}); }
  function ok(text) { const box = $("status-message"); box.textContent = text; box.hidden = false; $("app-error").hidden = true; }
  function button(text, fn, cls = "button button-quiet") { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", () => Promise.resolve().then(fn).catch(fail)); return b; }
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  async function api(path, body) {
    const r = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: body === undefined ? {} : {"Content-Type": "application/json", "X-CSRF-Token": csrf()},
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
    let d = {};
    try { d = await r.json(); } catch (_) { /* empty */ }
    if (r.status === 401) { location.href = "/login"; throw new Error("Please sign in again."); }
    if (!r.ok) throw new Error(d.error || `The request failed (${r.status}).`);
    return d;
  }
  const readFile = file => new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.onerror = reject; r.readAsDataURL(file); });
  const fmt = new Intl.DateTimeFormat("en-US", {month: "short", day: "numeric", year: "numeric"});
  function day(iso) { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || ""); return m ? fmt.format(new Date(+m[1], +m[2] - 1, +m[3])) : ""; }
  function dayTs(ts) { return ts ? fmt.format(new Date(ts * 1000)) : ""; }
  function size(n) { return !n ? "" : n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`; }
  const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;
  function link(href, text, download = true) { const a = el("a", "", text); a.href = href; if (download) a.setAttribute("download", ""); return a; }

  function num(value, cls = "") { return el("span", `rc-num ${cls}`.trim(), value || "No number"); }
  function src(key) { return key ? el("span", `rc-src src-${key}`, SOURCES[key] || key) : el("span", "muted", "—"); }
  function co(c, faint = false) {
    const chip = el("span", faint ? "rc-co faint" : "rc-co"); const dot = el("i");
    dot.style.background = `linear-gradient(135deg, ${c.color || "#1f55d6"}, ${c.accent || c.color || "#1f55d6"})`;
    chip.title = c.name; chip.append(dot, el("span", "", c.short || c.name)); return chip;
  }
  function docTags(states) {
    const box = el("div", "rc-docs");
    [["baa", "BAA"], ["services", "Services"], ["nda", "NDA"]].forEach(([k, label]) => {
      const s = states[k]; const tag = el("span", `rc-doc ${s === "signed" ? "" : s === "out" ? "wait" : "no"}`.trim(), label);
      tag.title = s === "signed" ? `Signed ${label} on file` : s === "out" ? `${label} out for signature` : `No signed ${label}`; box.append(tag);
    });
    return box;
  }
  function kpi(label, value, sub, tone = "") { const k = el("div", `rc-kpi ${tone}`.trim()); k.append(el("small", "", label), el("strong", "", value), el("span", "", sub)); return k; }
  function seg(options, current, onChange, label) {
    const box = el("div", "rc-seg"); box.setAttribute("role", "group"); box.setAttribute("aria-label", label);
    options.forEach(([key, text, count]) => {
      const b = el("button", "", text); b.type = "button"; b.setAttribute("aria-pressed", String(key === current));
      if (count !== undefined) b.append(el("em", "", count));
      b.addEventListener("click", () => Promise.resolve().then(() => onChange(key)).catch(fail)); box.append(b);
    });
    return box;
  }
  function selectOf(options, value, label) { const s = el("select"); options.forEach(([v, t]) => s.append(new Option(t, v))); s.value = value ?? ""; if (label) s.setAttribute("aria-label", label); return s; }
  function head(eyebrow, title, sub, actions) {
    const h = el("div", "page-head"); const t = el("div"); t.append(el("p", "eyebrow", eyebrow), el("h1", "", title), el("p", "page-sub", sub));
    const a = el("div", "rc-actions"); actions.forEach(x => x && a.append(x)); h.append(t, a); return h;
  }
  function table(headers) {
    const t = el("table", "rc-table"); const tr = el("tr"); headers.forEach(h => tr.append(el("th", "", h))); const th = el("thead"); th.append(tr);
    const tb = el("tbody"); t.append(th, tb); return [t, tb];
  }
  function rowLink(tr, fn) {
    tr.classList.add("rc-link"); tr.tabIndex = 0;
    tr.addEventListener("click", e => { if (!e.target.closest("a,button,select,input")) Promise.resolve().then(fn).catch(fail); });
    tr.addEventListener("keydown", e => { if (e.key === "Enter" && e.target === tr) Promise.resolve().then(fn).catch(fail); });
  }
  function cell(...children) { const td = el("td"); children.forEach(c => c && td.append(c)); return td; }
  function strongSmall(title, sub) { const f = document.createDocumentFragment(); f.append(el("strong", "", title)); if (sub) f.append(el("small", "", sub)); return f; }
  function testBadge(text = "TEST") { const b = el("span", "test-badge", text); b.title = "A practice run: kept out of the register, Home, Insights and reports"; return b; }
  const sendBaa = (clientId, entity) => window.openClientDraft(clientId, {entity, documents: ["baa"]});

  // ------------------------------------------------------------------ the clients register
  const reg = {q: "", company: "", source: "", baa: "all", test: "real", page: 0};
  let regTimer = null;
  async function register(view, ctx) {
    const qs = new URLSearchParams({q: reg.q, company: reg.company, source: reg.source, baa: reg.baa, test: reg.test, page: reg.page});
    const d = await api(`/api/client-records/register?${qs}`);
    reg.page = d.page;
    const wrap = el("div", "rc-wrap");
    const k = d.kpis;
    wrap.append(head("Your records", "Clients", "Every client in one register, each with a permanent client number. Agreements signed in this portal, in DocuSign, by email or through Jotform are all kept on the client's record.", [
      ctx.user.role === "super_admin" && ctx.config.local ? button("Import existing local contracts", ctx.importLegacy) : null,
      d.can_import ? button("Import previous clients", () => window.portalRoute("#/clients/import")) : null,
      button("Signed BAA register", () => window.portalRoute("#/clients/baa-register")),
      button("New client", ctx.newClient, "button button-primary")]));
    const kpis = el("div", "rc-kpis");
    const bySource = Object.entries(k.earlier_by_source).filter(([, n]) => n).map(([s, n]) => `${SOURCES[s]} ${n}`).join(" · ");
    kpis.append(kpi("Clients", k.clients, `${k.portal_only} portal only · ${k.earlier} with earlier records`),
      kpi("Signed BAAs on file", k.covered, `${k.coverage} % of clients covered`, "good"),
      kpi("Clients missing a signed BAA", k.missing, k.missing ? "Send one from the Signed BAA register" : "Every client is covered", k.missing ? "warn" : ""),
      kpi("From earlier systems", k.earlier, bySource || "DocuSign, manual (email) and Jotform"));
    wrap.append(kpis);
    const panel = el("section", "panel table-card");
    const filters = el("form", "rc-filters"); filters.setAttribute("role", "search");
    const q = el("input"); q.type = "search"; q.value = reg.q; q.placeholder = "Search by client number, name, NPI, email or address"; q.setAttribute("aria-label", "Search clients");
    q.addEventListener("input", () => { clearTimeout(regTimer); regTimer = setTimeout(() => { reg.q = q.value.trim(); reg.page = 0; ctx.refresh(true); }, 350); });
    filters.addEventListener("submit", e => { e.preventDefault(); clearTimeout(regTimer); reg.q = q.value.trim(); reg.page = 0; ctx.refresh(true); });
    const company = selectOf([["", "All companies"], ...d.companies.map(c => [c.name, c.short])], reg.company, "Company");
    company.addEventListener("change", () => { reg.company = company.value; reg.page = 0; ctx.refresh(); });
    const source = selectOf([["", "All sources"], ...Object.entries(SOURCES)], reg.source, "Signed through");
    source.addEventListener("change", () => { reg.source = source.value; reg.page = 0; ctx.refresh(); });
    const test = selectOf([["real", "Real clients"], ["test", "Test clients"], ["all", "Real and test clients"]], reg.test, "Real or test clients");
    test.addEventListener("change", () => { reg.test = test.value; reg.page = 0; ctx.refresh(); });
    filters.append(q, company, source, test, seg([["all", "All", d.counts.all], ["signed", "BAA signed", d.counts.signed], ["missing", "BAA missing", d.counts.missing]], reg.baa,
      key => { reg.baa = key; reg.page = 0; ctx.refresh(); }, "Signed BAA"));
    panel.append(filters);
    const body = el("div", "panel-body");
    if (!d.clients.length) body.append(el("p", "rc-empty", reg.q || reg.company || reg.source || reg.baa !== "all" ? "No clients match these filters." : "No clients yet. Create one, generate an agreement or import previous clients."));
    else {
      const [t, tb] = table(["Client no.", "Client", "Companies", "Agreements on file", "Signed BAA", "Signed through", "Last activity"]);
      d.clients.forEach(c => {
        const tr = el("tr"); if (c.test) tr.classList.add("row-test");
        const who = cell(strongSmall(c.name, [c.place, c.npi && `NPI ${c.npi}`].filter(Boolean).join(" · ")));
        if (c.test) who.querySelector("strong").append(testBadge("Test client"));
        const companies = cell(); c.companies.forEach(x => companies.append(co(x, !x.linked)));
        if (!c.companies.length) companies.append(el("span", "muted", "—"));
        const baa = cell(); baa.className = "d";
        if (c.baa) { baa.append(el("span", "rc-baa", `Signed ${day(c.baa.date) || "(date not recorded)"}`)); if (c.baa_missing_for.length) baa.append(el("small", "", `Missing for ${c.baa_missing_for.join(", ")}`)); }
        else baa.append(el("span", "rc-baa miss", "Missing"));
        const last = el("td", "d", dayTs(c.last_activity));
        tr.append(cell(num(c.client_number)), who, companies, cell(docTags(c.docs)), baa, cell(c.through ? src(c.through) : el("span", "muted", "—")), last);
        rowLink(tr, () => ctx.openClient(c.id)); tb.append(tr);
      });
      body.append(t);
    }
    panel.append(body);
    const pager = el("div", "rc-pager");
    const from = d.total ? d.page * d.page_size + 1 : 0, to = Math.min(d.total, (d.page + 1) * d.page_size);
    const nav = el("span"); const prev = button("Previous", () => { reg.page--; return ctx.refresh(); }, "button button-quiet button-sm"); prev.disabled = d.page === 0;
    const next = button("Next", () => { reg.page++; return ctx.refresh(); }, "button button-quiet button-sm"); next.disabled = to >= d.total; nav.append(prev, next);
    pager.append(el("span", "", `${from}–${to} of ${plural(d.total, "client")} · numbers are permanent and never reused`), nav);
    panel.append(pager); wrap.append(panel);
    const jobs = el("details", "card clients-jobs"); jobs.style.padding = "12px 14px"; jobs.append(el("summary", "", "Recent agreement generations"));
    jobs.addEventListener("toggle", async () => {
      if (!jobs.open || jobs.dataset.loaded) return; jobs.dataset.loaded = "1";
      const j = await api("/api/jobs");
      jobs.append(...(j.jobs.length ? j.jobs.map(x => el("p", "muted small", `${new Date(x.created * 1000).toLocaleString()} · ${x.state}${x.error ? ` · ${x.error}` : ""}`)) : [el("p", "muted small", "No recent generations.")]));
    });
    wrap.append(jobs);
    view.replaceChildren(wrap);
    return {focusSearch: () => { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }};
  }

  // ------------------------------------------------------------------ the client record: agreements and signed BAA by company
  function itemSub(x) {
    if (x.origin === "generated") return "Generated, not sent yet";
    if (x.origin === "portal") return x.status === "out" ? "Waiting for signatures" : `Envelope ${x.reference}`;
    if (x.source === "docusign") return x.reference ? `Envelope ${x.reference.slice(0, 13)}…` : "Signed in DocuSign";
    if (x.source === "jotform") return x.reference ? `Submission ${x.reference}` : "Submitted through Jotform";
    if (x.source === "manual") return "Returned by email";
    return x.reference || "Signed before this portal";
  }
  function agreementsTable(body, d, ctx, filter) {
    const items = d.items.filter(filter || (() => true));
    if (!items.length) {
      body.classList.remove("flush");
      body.append(el("p", "empty-state", filter ? "No signed Business Associate Agreement on file yet." : "No agreements yet. Create one, send documents for signature or add an agreement signed before this portal."));
      return;
    }
    const [t, tb] = table(["Agreement", "Company", "Signed through", "Signed", "Effective", "Files"]);
    items.forEach(x => {
      const tr = el("tr"); if (x.test) tr.classList.add("row-test");
      const what = cell(strongSmall(x.title, itemSub(x)));
      if (x.test) what.querySelector("strong").append(testBadge());
      const files = el("td", "rc-files");
      if (x.status === "out") files.append(el("span", "chip chip-warning", "Out for signature"));
      else if (STATUS[x.status]) files.append(el("span", "chip chip-muted", STATUS[x.status]));
      (x.links || []).forEach(l => files.append(link(l.url, l.label)));
      if (x.file) files.append(link(x.file.url, x.source === "manual" ? "Signed scan" : "Signed PDF"));
      if (x.origin === "generated") files.append(button("Send for signature", () => window.esignFromContract(x.contract_id), "text-button"));
      if (x.envelope_id && x.status !== "signed") files.append(button("Open", () => window.esignOpen(x.envelope_id), "text-button"));
      if (x.origin === "past" && x.editable) {
        const acts = el("div", "rc-row-actions");
        acts.append(button("Change", () => addPast(ctx.client, x, ctx.reload), "text-button"), button("Remove", async () => {
          if (!confirm(`Remove this ${KINDS[x.kind] || "agreement"} from the client's record? The signed file stays in the client's documents.`)) return;
          await api(`/api/clients/${ctx.client.id}/past-agreements/${x.id}`, {delete: true}); ok("Agreement removed from the record."); await ctx.reload();
        }, "text-button"));
        files.append(acts);
      }
      if (!files.childNodes.length) files.append(el("span", "muted", "—"));
      tr.append(what, cell(co(x.company)), cell(src(x.source)), el("td", "d", day(x.signed_on) || "—"), el("td", "d", day(x.effective_on) || "—"), files);
      if (x.envelope_id) rowLink(tr, () => window.esignOpen(x.envelope_id));
      tb.append(tr);
    });
    body.append(t);
  }
  function sidePanel(aside, d, client) {
    aside.append(el("h3", "", "Signed BAA by company"));
    d.companies.forEach(c => {
      const row = el("div", "rc-cov"); const left = el("div"); left.append(co(c));
      if (c.baa) { left.append(el("small", "", [SOURCES[c.baa.source], day(c.baa.date)].filter(Boolean).join(" · "))); row.append(left, el("span", "ok", "✓ On file")); }
      else if (c.out) { left.append(el("small", "", "Sent for signature")); row.append(left, el("span", "wait", "Waiting")); }
      else if (c.linked) { left.append(el("small", "", "No signed BAA yet")); row.append(left, button("Send a BAA", () => sendBaa(client.id, c.name), "button button-quiet button-sm")); }
      else { left.append(el("small", "", "No agreement with this company")); row.append(left, el("span", "muted", "—")); }
      aside.append(row);
    });
    const note = el("div", "rc-callout"); const text = el("div"); text.append(el("b", "", `Client number ${client.client_number || ""}`),
      document.createTextNode(" appears on this record, in search, on reports and exports. It never changes, even if the client's name does."));
    note.append(el("span", "rc-ic", "i"), text); aside.append(note);
  }

  // ------------------------------------------------------------------ add or change a past signed agreement
  function addPast(client, existing, done) {
    const entities = (window.Records.config?.entities || []);
    const box = el("dialog", "rc-dialog");
    const top = el("div", "rc-dialog-head"); const titles = el("div");
    titles.append(el("h2", "", existing ? "Change a past signed agreement" : "Add a past signed agreement"),
      el("p", "", "For agreements signed before this portal: returned by email, signed in DocuSign or submitted through Jotform. The original file is kept unchanged."));
    const close = el("button", "icon-button", "×"); close.type = "button"; close.setAttribute("aria-label", "Close"); close.addEventListener("click", () => box.close());
    top.append(titles, close);
    const form = el("form"); form.noValidate = true;
    const body = el("div", "rc-dialog-body"); const left = el("div", "rc-left"); const right = el("div", "rc-form");
    const message = el("p", "notice notice-danger rc-message"); message.hidden = true;
    let fileId = existing?.file?.id || null, uploadedHere = null, saved = false;
    // the form
    const fields = {};
    function field(label, control, full = false, key = null) {
      const f = el("div", full ? "rc-field full" : "rc-field"); const id = `rc-${Math.random().toString(36).slice(2)}`;
      const l = el("label", "", label); l.htmlFor = id; const wrapCtl = el("div", "rc-ctl"); control.id = id; wrapCtl.append(control); f.append(l, wrapCtl);
      if (key) { fields[key] = {control, wrap: wrapCtl}; control.addEventListener("input", () => tag(key, false)); control.addEventListener("change", () => tag(key, false)); }
      return f;
    }
    function tag(key, on) {
      const f = fields[key]; if (!f) return; f.wrap.querySelector(".rc-tag")?.remove(); f.wrap.classList.toggle("tagged", on);
      if (on) f.wrap.append(el("span", "rc-tag", "from file"));
    }
    const clientBox = el("div", "rc-client"); clientBox.append(num(client.client_number), el("span", "", client.name));
    const clientField = el("div", "rc-field full"); clientField.append(el("span", "rc-label", "Client"), clientBox);
    const company = selectOf([["", "Choose the company…"], ...entities.map(e => [e.name, e.short || e.name])], existing?.company?.name || (entities.length === 1 ? entities[0].name : ""));
    const kind = selectOf(Object.entries(KINDS), existing?.kind || "baa");
    const pills = el("fieldset", "rc-pills full"); pills.append(el("legend", "", "Signed through"));
    Object.entries(SOURCES).filter(([k]) => k !== "portal").forEach(([k, label]) => {
      const l = el("label"); const r = el("input"); r.type = "radio"; r.name = "source"; r.value = k; r.checked = (existing?.source || "manual") === k;
      r.addEventListener("change", () => { pills.querySelector(".rc-tag")?.remove(); summary(); }); l.append(r, document.createTextNode(label)); pills.append(l);
    });
    const signed = el("input"); signed.type = "date"; signed.max = new Date().toISOString().slice(0, 10); signed.value = existing?.signed_on || "";
    const effective = el("input"); effective.type = "date"; effective.value = existing?.effective_on || "";
    const signer = el("input"); signer.maxLength = 200; signer.value = existing?.client_signer || ""; signer.placeholder = "Name · title";
    const companySigner = el("input"); companySigner.maxLength = 200; companySigner.value = existing?.company_signer || ""; companySigner.placeholder = "Name · title";
    const reference = el("input"); reference.maxLength = 120; reference.value = existing?.reference || ""; reference.placeholder = "DocuSign envelope ID or Jotform submission ID";
    const notes = el("textarea"); notes.maxLength = 4000; notes.rows = 2; notes.value = existing?.notes || "";
    let autoSigner = !existing;
    function companyDefaults() {
      const e = entities.find(x => x.name === company.value);
      if (e && autoSigner && e.signatory) companySigner.value = [e.signatory, e.title].filter(Boolean).join(" · ");
    }
    companySigner.addEventListener("input", () => { autoSigner = false; });
    company.addEventListener("change", () => { companyDefaults(); summary(); }); kind.addEventListener("change", summary);
    right.append(clientField, field("Company", company, false, "entity"), field("Agreement", kind, false, "kind"), pills,
      field("Signed on", signed, false, "signed_on"), field("Effective date", effective, false, "effective_on"), field("Client signer", signer, false, "client_signer"),
      field("Signed for the company by", companySigner), field("Envelope or submission ID (optional)", reference, true, "reference"), field("Notes (optional)", notes, true));
    companyDefaults();
    // the file
    const fileArea = el("div", "rc-left");
    const found = el("div", "rc-found"); found.hidden = true;
    const hint = el("p", "rc-note", "Details are read from the file where it contains them (DocuSign and Jotform PDFs usually do). Check them before saving; a scanned paper copy is filled in by hand.");
    function fileCard(f) {
      const card = el("div", "rc-file"); const t = el("div"); t.append(el("b", "", f.name), el("small", "", [f.pages && plural(f.pages, "page"), size(f.size),
        f.scan === "clean" ? "virus scan passed" : f.scan === "not_scanned" ? "not virus-scanned (no scanner set up)" : ""].filter(Boolean).join(" · ")));
      card.append(el("span", "pdf", "PDF"), t); return card;
    }
    function dropZone(another = false) {
      const zone = el("label", "rc-drop"); const input = el("input"); input.type = "file"; input.accept = ".pdf,application/pdf";
      zone.append(el("strong", "", another ? "Choose another PDF" : existing ? "Replace the signed PDF" : "Choose the signed PDF"), el("span", "", "or drop it here · PDF up to 15 MB"), input);
      input.addEventListener("change", () => input.files[0] && upload(input.files[0]));
      zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("is-over"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
      zone.addEventListener("drop", e => { e.preventDefault(); zone.classList.remove("is-over"); const f = e.dataTransfer.files[0]; if (f) upload(f); });
      return zone;
    }
    function showFound(f) {
      found.replaceChildren(); found.hidden = false;
      const rows = [["Signed through", SOURCES[f.source]], [f.source === "jotform" ? "Submission ID" : "Envelope ID", f.reference],
        ["Company", entities.find(e => e.name === f.entity)?.name || f.entity], ["Agreement", KINDS[f.kind]],
        ["Client signer", [f.client_signer, f.client_title].filter(Boolean).join(", ")], [f.source === "docusign" ? "Completed" : "Signed", day(f.signed_on)],
        ["Effective", day(f.effective_on)]].filter(([, v]) => v);
      found.classList.toggle("empty", !rows.length);
      found.append(el("h4", "", rows.length ? "✓ Found in the file" : "Nothing found in the file"));
      if (!rows.length) found.append(el("p", "", f.text ? "The file has text but no agreement details we recognise. Type them in." : "No text could be read: it may be a scanned copy. Type the details."));
      rows.forEach(([k, v]) => { const r = el("div"); r.append(el("span", "", k), el("b", "", v)); found.append(r); });
    }
    function prefill(f) {
      const set = (key, control, value) => { if (!value) return; control.value = value; if (control.value === value) tag(key, true); };
      if (f.entity && entities.some(e => e.name === f.entity)) { set("entity", company, f.entity); companyDefaults(); }
      set("kind", kind, f.kind); set("signed_on", signed, f.signed_on); set("effective_on", effective, f.effective_on);
      set("client_signer", signer, [f.client_signer, f.client_title].filter(Boolean).join(" · ")); set("reference", reference, f.reference);
      if (f.source) { const r = pills.querySelector(`input[value="${f.source}"]`); if (r) { r.checked = true; pills.querySelector(".rc-tag")?.remove(); pills.append(el("span", "rc-tag rc-pill-tag", "from file")); } }
      summary();
    }
    async function upload(file) {
      message.hidden = true;
      if (!/\.pdf$/i.test(file.name)) { message.textContent = "Choose a PDF file."; message.hidden = false; return; }
      if (file.size > 15000000) { message.textContent = "Choose a file smaller than 15 MB."; message.hidden = false; return; }
      fileArea.replaceChildren(el("p", "rc-note", `Uploading ${file.name} and checking it for viruses…`));
      try {
        const up = await api(`/api/clients/${client.id}/files`, {name: file.name, data: await readFile(file), category: "contract", note: "Signed agreement from before this portal"});
        if (uploadedHere && uploadedHere !== up.id) api(`/api/clients/files/${uploadedHere}/delete`, {}).catch(() => {});
        uploadedHere = up.id; fileId = up.id;
        fileArea.replaceChildren(el("p", "rc-note", "Reading the file…"));
        const read = await api(`/api/clients/${client.id}/past-agreements/read`, {file_id: up.id});
        fileArea.replaceChildren(fileCard(read.file), dropZone(true)); showFound(read.found); prefill(read.found);
      } catch (e) { fileArea.replaceChildren(dropZone()); message.textContent = e.message; message.hidden = false; }
    }
    if (existing?.file) fileArea.append(fileCard(existing.file), dropZone()); else fileArea.append(dropZone());
    left.append(fileArea, found, hint);
    body.append(left, right);
    // footer
    const foot = el("div", "rc-dialog-foot"); const note = el("span", "rc-note");
    function summary() {
      const s = pills.querySelector("input:checked")?.value || "manual";
      note.textContent = [`Saved to ${client.client_number || "the client's record"}`, kind.value === "baa" ? "shows in the Signed BAA register" : "",
        `listed as "Signed through ${SOURCES[s]}"`].filter(Boolean).join(" · ");
    }
    const actions = el("div", "rc-actions"); const cancel = el("button", "button button-quiet", "Cancel"); cancel.type = "button"; cancel.addEventListener("click", () => box.close());
    const save = el("button", "button button-primary", existing ? "Save changes" : "Save to client record"); save.type = "submit";
    actions.append(cancel, save); foot.append(note, actions); summary();
    form.append(body, message, foot);
    form.addEventListener("submit", async e => {
      e.preventDefault(); message.hidden = true; save.disabled = true;
      try {
        if (!company.value) throw new Error("Choose the company the agreement was signed with.");
        const payload = {entity: company.value, kind: kind.value, source: pills.querySelector("input:checked")?.value || "manual", signed_on: signed.value, effective_on: effective.value,
          client_signer: signer.value.trim(), company_signer: companySigner.value.trim(), reference: reference.value.trim(), notes: notes.value.trim(), file_id: fileId};
        if (existing) await api(`/api/clients/${client.id}/past-agreements/${existing.id}`, payload);
        else await api(`/api/clients/${client.id}/past-agreements`, payload);
        saved = true; box.close(); ok(existing ? "Agreement saved." : `Agreement saved to ${client.client_number || "the client's record"}.`); await done();
      } catch (err) { message.textContent = err.message; message.hidden = false; } finally { save.disabled = false; }
    });
    box.append(top, form); document.body.append(box);
    box.addEventListener("close", () => { if (uploadedHere && !saved) api(`/api/clients/files/${uploadedHere}/delete`, {}).catch(() => {}); box.remove(); });
    box.showModal();
  }

  // ------------------------------------------------------------------ import previous clients
  const imp = {view: null, step: 1, filter: "all", shown: 100, uploads: []};
  async function importPage(view) {
    imp.data = await api("/api/client-imports", {});
    imp.step = imp.data.files || imp.data.sheet ? 2 : 1; imp.filter = "all"; imp.uploads = []; imp.view = view;
    renderImport();
  }
  function renderImport() {
    const view = imp.view, d = imp.data; const wrap = el("div", "rc-wrap");
    wrap.append(head("Clients", "Import previous clients", "Bring in clients and signed agreements from before this portal: the manual email method, DocuSign and Jotform. Nothing is saved until you select Import.", [
      button("← All clients", () => window.portalRoute("#/clients")), link("/api/client-imports/template.xlsx", "Download the spreadsheet template")]));
    wrap.querySelector(".rc-actions a").className = "button button-quiet";
    const steps = el("div", "rc-steps");
    [["Upload files", 1], ["Check the matches", 2], ["Import", 3]].forEach(([label, n]) => {
      const s = el("div", `rc-step ${imp.step > n ? "done" : imp.step === n ? "on" : ""}`.trim()); s.append(el("i", "", imp.step > n ? "✓" : n), document.createTextNode(label)); steps.append(s);
    });
    wrap.append(steps);
    if (imp.step === 3) wrap.append(importDone());
    else if (imp.step === 1) wrap.append(importUpload());
    else wrap.append(importMatches());
    view.replaceChildren(wrap);
  }
  function importUpload() {
    const d = imp.data; const panel = el("section", "panel"); const grid = el("div", "rc-upload");
    const sheetCol = el("div"); sheetCol.append(el("h3", "", "Spreadsheet (optional)"),
      el("p", "", "A list of previous clients: client name, address, NPI, email, company, agreement, signed date and how it was signed. Use the template or your own headings."));
    if (d.sheet) {
      const card = el("div", "rc-file"); const t = el("div"); t.append(el("b", "", d.sheet.name), el("small", "", `${plural(d.sheet.rows, "row")} · ${plural(d.sheet.columns, "column")}`));
      const pdf = el("span", "pdf xlsx", d.sheet.name.toLowerCase().endsWith(".csv") ? "CSV" : "XLSX"); card.append(pdf, t); sheetCol.append(card);
      sheetCol.append(button("Remove the spreadsheet", async () => { imp.data = await api(`/api/client-imports/${d.id}/sheet`, {remove: true}); renderImport(); }, "text-button"));
    } else sheetCol.append(fileDrop("Choose a spreadsheet", ".xlsx or .csv", ".xlsx,.csv", false, files => uploadSheet(files[0])));
    const pdfCol = el("div"); pdfCol.append(el("h3", "", "Signed agreements (PDF)"),
      el("p", "", `Any number of signed PDFs, up to ${d.limits.files} per import and 15 MB each. Each one is checked for viruses and read for the client, company, agreement and date.`));
    pdfCol.append(fileDrop("Choose signed PDFs", "PDF files · several at once", ".pdf,application/pdf", true, uploadPdfs));
    if (d.files) pdfCol.append(el("p", "rc-note", `${plural(d.files, "file")} added so far.`));
    const progress = el("div", "rc-progress"); progress.id = "rc-progress";
    imp.uploads.forEach(u => { const r = el("div"); r.append(el("span", "", u.name), el("span", u.error ? "bad" : u.done ? "good" : "", u.error || (u.done ? "Added" : "Uploading…"))); progress.append(r); });
    pdfCol.append(progress);
    grid.append(sheetCol, pdfCol); panel.append(grid);
    const pager = el("div", "rc-pager"); const nav = el("span");
    const cancel = button("Cancel import", cancelImport, "button button-quiet button-sm");
    const next = button("Check the matches", () => { imp.step = 2; renderImport(); }, "button button-primary button-sm"); next.disabled = !d.files && !d.sheet;
    nav.append(cancel, next); pager.append(el("span", "", "Nothing is saved to the client records until you select Import."), nav); panel.append(pager);
    return panel;
  }
  function fileDrop(title, sub, accept, multiple, onFiles) {
    const zone = el("label", "rc-drop"); const input = el("input"); input.type = "file"; input.accept = accept; input.multiple = multiple;
    zone.append(el("strong", "", title), el("span", "", `or drop ${multiple ? "them" : "it"} here · ${sub}`), input);
    input.addEventListener("change", () => input.files.length && Promise.resolve().then(() => onFiles([...input.files])).catch(fail));
    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("is-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
    zone.addEventListener("drop", e => { e.preventDefault(); zone.classList.remove("is-over"); if (e.dataTransfer.files.length) Promise.resolve().then(() => onFiles([...e.dataTransfer.files])).catch(fail); });
    return zone;
  }
  async function uploadSheet(file) {
    if (file.size > 5000000) throw new Error("Choose a spreadsheet smaller than 5 MB.");
    imp.data = await api(`/api/client-imports/${imp.data.id}/sheet`, {name: file.name, data: await readFile(file)}); renderImport();
  }
  async function uploadPdfs(files) {
    const list = files.map(f => ({file: f, name: f.name, done: false, error: ""})); imp.uploads = list.concat(imp.uploads.filter(u => u.error)); renderImport();
    let next = 0;
    async function worker() {
      while (next < list.length) {
        const u = list[next++];
        try {
          if (!/\.pdf$/i.test(u.name)) throw new Error("Not a PDF");
          if (u.file.size > 15000000) throw new Error("Larger than 15 MB");
          await api(`/api/client-imports/${imp.data.id}/files`, {name: u.name, data: await readFile(u.file)}); u.done = true;
        } catch (e) { u.error = e.message; }
        const p = $("rc-progress"); if (p) { const i = imp.uploads.indexOf(u); const row = p.children[i]; if (row) { row.lastChild.textContent = u.error || "Added"; row.lastChild.className = u.error ? "bad" : "good"; } }
      }
    }
    await Promise.all([worker(), worker(), worker()]);
    imp.data = await api(`/api/client-imports/${imp.data.id}`);
    const failed = list.filter(u => u.error).length;
    if (!failed) { ok(`${plural(list.length, "file")} added.`); imp.uploads = []; }
    renderImport();
  }
  async function cancelImport() {
    if (!confirm("Cancel this import? The uploaded files are discarded; nothing was saved to the client records.")) return;
    await api(`/api/client-imports/${imp.data.id}/cancel`, {}); ok("Import cancelled."); window.portalRoute("#/clients");
  }
  async function choose(key, body) { imp.data = await api(`/api/client-imports/${imp.data.id}/choices`, Object.assign({key}, body)); renderImport(); }
  function clientCell(it) {
    const m = it.match, td = el("td");
    const picker = (cls = "rc-choose") => {
      const s = el("select", cls); s.setAttribute("aria-label", `Client for ${it.file ? it.file.name : `row ${it.row}`}`);
      s.append(new Option(m.state === "choose" ? "Choose a client…" : "Change…", ""));
      (m.candidates || []).forEach(c => s.append(new Option(`${c.client_number} · ${c.name}`, `id:${c.id}`)));
      if (m.client && !(m.candidates || []).some(c => c.id === m.client.id)) s.append(new Option(`${m.client.client_number} · ${m.client.name}`, `id:${m.client.id}`));
      const guess = m.new || (it.file ? "" : it.row_name);
      if (guess) s.append(new Option(`New client: ${guess}`, "new"));
      s.append(new Option("New client with another name…", "new-name"), new Option("Another client, by client number…", "number"), new Option("Skip this file", "skip"));
      if (m.state === "skip") s.append(new Option("Include it again", "clear"));
      s.addEventListener("change", () => Promise.resolve().then(async () => {
        const v = s.value; if (!v) return;
        if (v.startsWith("id:")) return choose(it.key, {client_id: v.slice(3)});
        if (v === "new") return choose(it.key, {new: guess});
        if (v === "skip") return choose(it.key, {skip: true});
        if (v === "clear") return choose(it.key, {clear: true});
        if (v === "new-name") { const name = prompt("Name of the new client", guess || ""); if (name && name.trim()) return choose(it.key, {new: name.trim()}); s.value = ""; return; }
        if (v === "number") { const n = prompt("Client number (for example CL-0012)", ""); if (n && n.trim()) return choose(it.key, {client_number: n.trim()}); s.value = ""; }
      }).catch(e => { s.value = ""; fail(e); }));
      return s;
    };
    if (m.state === "choose") { td.append(picker()); return td; }
    if (m.client) { const line = el("div"); line.append(num(m.client.client_number), document.createTextNode(" " + m.client.name)); td.append(line); }
    else if (m.state === "new") { const line = el("div"); line.append(num(m.client_number, "new"), document.createTextNode(" " + m.new)); td.append(line); }
    else if (m.state === "skip") td.append(el("span", "muted", m.note || "Not imported"));
    const change = button("Change", () => { change.replaceWith(picker("rc-choose plain")); }, "text-button"); change.style.fontSize = "12px"; td.append(change);
    return td;
  }
  function matchCell(m) {
    if (m.state === "matched") return el("span", "rc-conf good", `✓ Matched (${REASONS[m.reason] || m.reason})`);
    if (m.state === "new") return el("span", "rc-conf new", "+ New client");
    if (m.state === "skip") return el("span", "rc-conf skip", m.reason === "already on file" ? "Already on file" : "Skipped");
    return el("span", "rc-conf ask", (m.candidates || []).length > 1 ? `! ${m.candidates.length} possible clients` : "! Choose a client");
  }
  function foundText(it) {
    const a = it.agreement;
    if (!a) return it.type === "row" ? "Client details only" : "";
    const bits = [];
    if (it.type === "file" && !it.text) bits.push("Scanned copy · no text found");
    bits.push((a.defaulted.includes("source") ? "" : SOURCES[a.source]) || "", a.defaulted.includes("entity") ? "" : a.company, a.defaulted.includes("kind") ? "" : SHORT_KINDS[a.kind], day(a.signed_on));
    const text = bits.filter(Boolean).join(" · ");
    return text || "Nothing found";
  }
  function importMatches() {
    const d = imp.data; const grid = el("div", "rc-import");
    // what was uploaded
    const aside = el("aside", "panel rc-source"); aside.append(el("h3", "", "What was uploaded"));
    if (d.sheet) {
      const card = el("div", "rc-file"); const t = el("div"); t.append(el("b", "", d.sheet.name), el("small", "", `${plural(d.sheet.rows, "row")} · ${plural(d.sheet.columns, "column")}`));
      card.append(el("span", "pdf xlsx", d.sheet.name.toLowerCase().endsWith(".csv") ? "CSV" : "XLSX"), t); aside.append(card);
      const map = el("div", "rc-map"); d.sheet.mapping.forEach(([h, f]) => { map.append(el("span", "", h || "(no heading)"), el("span", "arrow", "→")); const to = el("span", f ? "" : "unused"); if (f) to.append(el("b", "", f)); else to.textContent = "not used"; map.append(to); });
      aside.append(map);
    }
    const bySource = Object.entries(d.by_source).filter(([, n]) => n).map(([s, n]) => `${SOURCES[s] || "Unknown"} ${n}`).join(" · ");
    const card = el("div", "rc-file"); const t = el("div"); t.append(el("b", "", `${plural(d.files, "signed agreement")}`), el("small", "", bySource || "No PDFs yet"));
    card.append(el("span", "pdf", "PDF"), t); aside.append(card);
    aside.append(el("h4", "", "When a file does not say"));
    const defs = el("div", "rc-defaults");
    const dCompany = selectOf([["", "Choose the company…"], ...d.companies.map(c => [c.name, c.short])], d.defaults.entity, "Company");
    if (d.needs_company && !d.defaults.entity) dCompany.classList.add("need");
    const dKind = selectOf(Object.entries(d.kinds), d.defaults.kind, "Agreement"); const dSource = selectOf(Object.entries(d.sources), d.defaults.source, "Signed through");
    const saveDefaults = () => api(`/api/client-imports/${d.id}/choices`, {defaults: {entity: dCompany.value, kind: dKind.value, source: dSource.value}}).then(x => { imp.data = x; renderImport(); }).catch(fail);
    [dCompany, dKind, dSource].forEach(s => s.addEventListener("change", saveDefaults));
    [["Company", dCompany], ["Agreement", dKind], ["Signed through", dSource]].forEach(([label, s]) => { const l = el("label", "", label); l.append(s); defs.append(l); });
    aside.append(defs);
    const newCount = d.new_clients.length;
    const call = el("div", "rc-callout ok"); const txt = el("div");
    txt.append(document.createTextNode("Clients already in the portal keep their numbers. "));
    if (newCount) { txt.append(el("b", "", plural(newCount, "new client")), document.createTextNode(` get${newCount === 1 ? "s" : ""} the next number${newCount === 1 ? "" : "s"} (${d.new_clients[0].number} onward), oldest signature first.`)); }
    else txt.append(document.createTextNode("No new clients will be created."));
    call.append(el("span", "rc-ic", "✓"), txt); aside.append(call);
    const blocked = d.needs_company && !d.defaults.entity;
    if (blocked) {
      const warn = el("div", "rc-callout warn"); warn.append(el("span", "rc-ic", "!"), el("div", "", `${plural(d.needs_company, "file does", "files do")} not say which company ${d.needs_company === 1 ? "it was" : "they were"} signed with. Choose the company above to use for ${d.needs_company === 1 ? "it" : "them"}; you can change any agreement later on the client's record.`));
      aside.insertBefore(warn, defs);
    }
    const more = el("div", "rc-actions"); more.append(button("Add more files", () => { imp.step = 1; renderImport(); }, "button button-quiet button-sm"), button("Cancel import", cancelImport, "button button-quiet button-sm"));
    aside.append(more);
    // the matches
    const panel = el("section", "panel table-card"); const filters = el("div", "rc-filters");
    const c = d.counts;
    filters.append(seg([["all", "All files", c.all], ["matched", "Matched", c.matched], ["new", "New clients", c.new], ["choose", "Need your choice", c.choose], ["skip", "Skipped", c.skip]],
      imp.filter, key => { imp.filter = key; imp.shown = 100; renderImport(); }, "Show"), el("p", "rc-note", "Matched by the client name in the file, the file name, the DocuSign envelope or the spreadsheet."));
    panel.append(filters);
    const items = d.items.filter(it => imp.filter === "all" || it.match.state === imp.filter);
    const body = el("div", "panel-body");
    if (!items.length) body.append(el("p", "rc-empty", d.items.length ? "Nothing here." : "Upload signed PDFs or a spreadsheet to see the matches."));
    else {
      const [tbl, tb] = table(["File", "Found in the file", "Client", "Match"]);
      items.slice(0, imp.shown).forEach(it => {
        const tr = el("tr");
        const fileCell = cell(strongSmall(it.file ? it.file.name : `Spreadsheet row ${it.row}`, it.file ? [size(it.file.size), it.row ? `with spreadsheet row ${it.row}` : ""].filter(Boolean).join(" · ") : it.row_name));
        const foundCell = el("td", "", foundText(it));
        if (it.agreement && it.agreement.from_client.length) foundCell.append(el("small", "", `company from the client's record: ${it.agreement.company}`));
        if (it.agreement && it.agreement.defaulted.length) foundCell.append(el("small", "", `${it.agreement.defaulted.map(k => ({entity: "company", kind: "agreement", source: "signed through"}[k])).join(", ")} from your choice`));
        tr.append(fileCell, foundCell, clientCell(it), cell(matchCell(it.match)));
        if (it.match.note) tr.lastChild.append(el("small", "", it.match.note));
        tb.append(tr);
      });
      body.append(tbl);
      if (items.length > imp.shown) body.append(button(`Show ${Math.min(100, items.length - imp.shown)} more`, () => { imp.shown += 100; renderImport(); }, "button button-quiet button-sm"));
    }
    panel.append(body);
    const pager = el("div", "rc-pager"); const nav = el("span");
    const back = button("Back", () => { imp.step = 1; renderImport(); }, "button button-quiet button-sm");
    const go = button(`Import ${plural(d.agreements, "agreement")}`, commitImport, "button button-primary button-sm");
    go.disabled = !d.items.some(it => ["matched", "new"].includes(it.match.state)) || c.choose > 0 || blocked;
    if (c.choose) go.title = "Choose a client for every file marked \"Choose a client\", or skip it.";
    else if (blocked) go.title = "Choose the company for the files that do not say which company they were signed with.";
    nav.append(back, go);
    pager.append(el("span", "", `Showing ${Math.min(items.length, imp.shown)} of ${items.length} · originals are stored unchanged, each marked with how it was signed`), nav);
    panel.append(pager);
    grid.append(aside, panel); return grid;
  }
  async function commitImport() {
    const d = imp.data;
    if (!confirm(`Import ${plural(d.agreements, "agreement")}${d.new_clients.length ? ` and create ${plural(d.new_clients.length, "new client")}` : ""}?`)) return;
    imp.result = await api(`/api/client-imports/${d.id}/commit`, {}); imp.step = 3; renderImport(); window.scrollTo(0, 0);
  }
  function importDone() {
    const r = imp.result || {}; const panel = el("section", "panel rc-done");
    panel.append(el("h2", "", "Import finished"), el("p", "", `${plural(r.clients_created || 0, "new client")} created, ${plural(r.agreements || 0, "agreement")} and ${plural(r.files || 0, "signed copy", "signed copies")} saved to the client records.${r.details_filled ? ` Missing details filled in for ${plural(r.details_filled, "existing client")}.` : ""}`));
    const a = el("div", "rc-actions"); a.append(button("Open the clients register", () => window.portalRoute("#/clients"), "button button-primary"), button("Signed BAA register", () => window.portalRoute("#/clients/baa-register")));
    panel.append(a); return panel;
  }

  // ------------------------------------------------------------------ the Signed BAA register
  const baa = {company: "", q: "", status: "all", page: 0};
  let baaTimer = null;
  async function baaRegister(view) {
    const qs = new URLSearchParams({company: baa.company, q: baa.q, status: baa.status, page: baa.page});
    const d = await api(`/api/client-records/baa-register?${qs}`);
    baa.company = d.company; baa.page = d.page;
    const wrap = el("div", "rc-wrap");
    const tab = d.tabs.find(t => t.name === d.company) || {short: "", signed: 0, total: 0};
    const exportUrl = format => `/api/client-records/baa-register/export?${new URLSearchParams({company: d.company, format, q: baa.q, status: baa.status})}`;
    const xlsx = link(exportUrl("xlsx"), "Export register (.xlsx)"); xlsx.className = "button button-quiet";
    const csv = link(exportUrl("csv"), "Export (.csv)"); csv.className = "button button-quiet";
    const zip = link(`/api/client-records/baa-register/files.zip?${new URLSearchParams({company: d.company})}`, "Download all signed BAAs (.zip)"); zip.className = "button button-primary";
    if (!tab.signed) { zip.removeAttribute("href"); zip.setAttribute("aria-disabled", "true"); zip.classList.add("is-disabled"); zip.title = "No signed BAA for this company yet"; }
    if (!d.company) [xlsx, csv].forEach(a => a.removeAttribute("href"));
    wrap.append(head("Clients", "Signed BAA register", "Every signed Business Associate Agreement, for every company and client, wherever it was signed. Export it for an audit or download every signed copy at once.",
      [button("← All clients", () => window.portalRoute("#/clients")), xlsx, csv, zip]));
    const k = d.kpis; const kpis = el("div", "rc-kpis");
    const by = Object.entries(k.earlier_by_source).filter(([, n]) => n).map(([s, n]) => `${SOURCES[s]} ${n}`).join(" · ");
    kpis.append(kpi("BAA coverage", `${k.coverage} %`, `${k.covered} of ${plural(k.clients, "client")}`, "good"), kpi("Signed in this portal", k.portal, "with Certificates of Completion"),
      kpi("From earlier systems", k.earlier, by || "DocuSign, manual (email) and Jotform"), kpi("Not signed yet", k.missing, "clients listed under \"Not signed\"", k.missing ? "warn" : ""));
    wrap.append(kpis);
    const panel = el("section", "panel table-card");
    const tabs = el("div", "rc-cotabs"); tabs.setAttribute("role", "group"); tabs.setAttribute("aria-label", "Company");
    d.tabs.forEach(t => {
      const b = el("button", "rc-cotab"); b.type = "button"; b.setAttribute("aria-pressed", String(t.name === d.company)); const dot = el("i");
      dot.style.background = `linear-gradient(135deg, ${t.color}, ${t.accent})`; b.append(dot, document.createTextNode(t.short), el("em", "", `${t.signed}/${t.total}`));
      b.addEventListener("click", () => { baa.company = t.name; baa.page = 0; baa.q = ""; baa.status = "all"; baaRegister(view).catch(fail); }); tabs.append(b);
    });
    panel.append(tabs);
    const filters = el("form", "rc-filters"); filters.setAttribute("role", "search");
    const q = el("input"); q.type = "search"; q.value = baa.q; q.placeholder = "Search client number or name"; q.setAttribute("aria-label", "Search clients");
    q.addEventListener("input", () => { clearTimeout(baaTimer); baaTimer = setTimeout(() => { baa.q = q.value.trim(); baa.page = 0; baaRegister(view).then(() => { const n = view.querySelector('input[type="search"]'); if (n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }).catch(fail); }, 350); });
    filters.addEventListener("submit", e => { e.preventDefault(); baa.q = q.value.trim(); baa.page = 0; baaRegister(view).catch(fail); });
    const cov = el("span", "rc-coverage"); const bar = el("span", "rc-bar"); const fill = el("i"); fill.style.width = `${d.coverage}%`; bar.append(fill);
    cov.append(document.createTextNode(`${tab.short} coverage`), bar, document.createTextNode(`${d.coverage} %`));
    filters.append(q, seg([["all", "All", d.counts.all], ["signed", "Signed", d.counts.signed], ["missing", "Not signed", d.counts.missing]], baa.status,
      key => { baa.status = key; baa.page = 0; return baaRegister(view); }, "Signed or not"), cov);
    panel.append(filters);
    const body = el("div", "panel-body");
    if (!d.rows.length) body.append(el("p", "rc-empty", d.tabs.length ? (baa.q || baa.status !== "all" ? "No clients match." : "No clients with this company yet.") : "You are not assigned to a company."));
    else {
      const [t, tb] = table(["Client no.", "Client", "BAA signed", "Signed through", "Signed by", "Effective", "File"]);
      d.rows.forEach(r => {
        const tr = el("tr");
        const signedCell = el("td", "d"); signedCell.append(el("span", r.signed ? "rc-baa" : "rc-baa miss", r.signed ? (day(r.date) || "Date not recorded") : "Not signed"));
        const through = cell(r.signed ? src(r.source) : button("Send a BAA", () => sendBaa(r.id, d.company), "button button-quiet button-sm"));
        const files = el("td", "rc-files");
        if (r.signed) {
          files.append(button("Open", () => r.envelope_id ? window.esignOpen(r.envelope_id) : window.portalRoute(`#/clients/${r.id}`), "text-button"));
          r.files.forEach((f, i) => files.append(link(f.url, r.files.length > 1 ? `Download ${i + 1}` : "Download")));
        } else files.append(el("span", "muted", "—"));
        const nameCell = cell(el("strong", "", r.name)); rowLink(tr, () => window.portalRoute(`#/clients/${r.id}`));
        tr.append(cell(num(r.client_number)), nameCell, signedCell, through, el("td", "", r.signed_by || "—"), el("td", "d", day(r.effective_on) || "—"), files);
        tb.append(tr);
      });
      body.append(t);
    }
    panel.append(body);
    const pager = el("div", "rc-pager"); const from = d.total ? d.page * d.page_size + 1 : 0, to = Math.min(d.total, (d.page + 1) * d.page_size);
    const nav = el("span"); const prev = button("Previous", () => { baa.page--; return baaRegister(view); }, "button button-quiet button-sm"); prev.disabled = d.page === 0;
    const next = button("Next", () => { baa.page++; return baaRegister(view); }, "button button-quiet button-sm"); next.disabled = to >= d.total; nav.append(prev, next);
    pager.append(el("span", "", `${from}–${to} of ${plural(d.total, "client")} with ${tab.short || "this company"} · test clients are left out`), nav); panel.append(pager);
    wrap.append(panel); view.replaceChildren(wrap);
  }

  window.Records = {register, agreementsTable, sidePanel, addPast, importPage, baaRegister, num, co, src, day, config: null,
    resetRegister: () => Object.assign(reg, {q: "", company: "", source: "", baa: "all", test: "real", page: 0})};
})();
