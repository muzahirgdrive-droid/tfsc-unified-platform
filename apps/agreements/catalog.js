/* Admin › Services & pricing, Admin › Agreement types and Admin › System check (Super Admin). */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; };
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { window.tfscSignIn?.("expired"); throw new Error("Please sign in again."); }
    if (!r.ok) throw new Error(d.error || "The request could not be completed.");
    return d;
  }
  const view = () => $("admin-view");
  function head(title, sub, buttonText, onClick) {
    const h = el("div", "page-head"); const t = el("div");
    t.append(el("p", "eyebrow", "Admin"), el("h1", "", title), el("p", "page-sub", sub)); h.append(t);
    if (buttonText) { const b = el("button", "button button-primary", buttonText); b.type = "button"; b.addEventListener("click", onClick); h.append(b); }
    return h;
  }
  function field(label, control, note) {
    const f = el("div", "cat-f"); const l = el("label", "", label);
    if (control.id) l.htmlFor = control.id;
    f.append(l, control); if (note) f.append(el("p", "cat-note", note)); return f;
  }
  let seq = 0;
  function input(value, attrs = {}) { const i = el("input"); i.type = "text"; i.id = "cat-" + (++seq); i.value = value ?? ""; Object.assign(i, attrs); return i; }
  function area(value, rows = 4, attrs = {}) { const t = el("textarea"); t.id = "cat-" + (++seq); t.value = value ?? ""; t.rows = rows; Object.assign(t, attrs); return t; }
  function select(options, value) { const s = el("select"); s.id = "cat-" + (++seq); options.forEach(([v, l]) => { const o = el("option", "", l); o.value = v; s.append(o); }); s.value = value; return s; }
  function check(title, sub, checked, value) {
    const l = el("label", "cat-check"); const i = el("input"); i.type = "checkbox"; i.checked = !!checked; if (value !== undefined) i.value = value;
    const t = el("span"); t.append(el("strong", "", title)); if (sub) t.append(el("small", "", sub)); l.append(i, t); return l;
  }
  function toggle(label, on) { const l = el("label", "cat-switch"); const i = el("input"); i.type = "checkbox"; i.checked = !!on; l.append(i, document.createTextNode(label)); return l; }
  function chip(text, cls = "") { return el("span", "cat-chip " + cls, text); }
  function msg(box, text, good) { box.className = "cat-msg " + (good ? "good" : "bad"); box.textContent = text; }
  function card(title, sub) { const c = el("section", "cat-card"); const h = el("header"); const t = el("div"); t.append(el("h2", "", title)); if (sub) t.append(el("p", "", sub)); h.append(t); c.append(h); const body = el("div", "cat-body"); c.append(body); return [c, body]; }
  function actions(card, ...buttons) { const a = el("div", "cat-actions"); a.append(el("span", "sp")); buttons.forEach(b => a.append(b)); card.append(a); return a; }
  function button(text, cls, fn) { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", fn); return b; }
  const CATS = [["recurring", "Recurring engagement"], ["on_demand", "On-demand project"]];
  const KINDS = {amount: "Amount in USD", percent: "Percent", number: "Number", date: "Date", text: "Short text", choice: "Choice from a list"};

  // ---------------------------------------------------------------- Services & pricing
  let catalog = null, tab = "services";
  async function services(selected) {
    window.portalMark?.("#/admin/services");
    catalog = await api("/api/admin/catalog");
    const v = view(); v.replaceChildren();
    v.append(head("Services & pricing", "The services your agreements can include, what each one covers and how it may be priced. Changes apply to agreements created from now on; agreements already generated or signed never change.",
      tab === "services" ? "+ Add a service" : "+ Add a pricing model", () => tab === "services" ? editService(null) : editModel(null)));
    const tabs = el("div", "cat-tabs"); tabs.setAttribute("role", "group");
    [["services", `Services (${catalog.services.length})`], ["models", `Pricing models (${catalog.models.length})`]].forEach(([k, label]) => {
      const b = button(label, "", () => { tab = k; services(); }); b.setAttribute("aria-pressed", String(tab === k)); tabs.append(b);
    });
    const cols = el("div", "cat-cols"); const left = el("section", "cat-card"); const right = el("div"); right.id = "cat-editor";
    cols.append(left, right); v.append(tabs, cols);
    const labels = Object.fromEntries(catalog.models.map(m => [m.id, m.label]));
    const table = el("table", "cat-table"); const thead = el("thead"); const tr = el("tr"); const tbody = el("tbody");
    if (tab === "services") {
      ["Service", "Type", "Pricing models offered", "In the builder"].forEach(x => tr.append(el("th", "", x)));
      catalog.services.forEach(s => {
        const row = el("tr"); if (selected === s.id) row.className = "sel";
        const name = el("td"); name.append(el("b", "", s.name)); if (s.description) name.append(el("small", "", s.description)); if (s.added) name.append(chip("Added by you", "new"));
        const kind = el("td"); kind.append(chip(s.category === "recurring" ? "Recurring" : "On demand", s.category === "recurring" ? "blue" : "grey"));
        const models = el("td"); s.models.forEach(m => models.append(chip(labels[m] || m)));
        const on = el("td", s.active ? "cat-ok" : "", s.active ? "Shown" : "Retired");
        row.append(name, kind, models, on); row.addEventListener("click", () => editService(s)); tbody.append(row);
      });
    } else {
      ["Pricing model", "Kind", "Used by"].forEach(x => tr.append(el("th", "", x)));
      catalog.models.forEach(m => {
        const row = el("tr"); if (selected === m.id) row.className = "sel";
        const name = el("td"); name.append(el("b", "", m.label)); if (m.wording) name.append(el("small", "", m.wording));
        row.append(name, el("td", "", m.builtin ? "Standard (worded by the system)" : "Added by you"), el("td", "", `${m.used} service${m.used === 1 ? "" : "s"}`));
        row.addEventListener("click", () => m.builtin ? standardModel(m) : editModel(m)); tbody.append(row);
      });
    }
    thead.append(tr); table.append(thead, tbody); left.append(table);
    const first = tab === "services" ? catalog.services.find(s => s.id === selected) || catalog.services[0] : catalog.models.find(m => m.id === selected && !m.builtin);
    if (tab === "services" && first) editService(first); else if (first) editModel(first); else if (tab === "models") standardModel(catalog.models[0]);
  }
  function editService(s) {
    const box = $("cat-editor"); const [c, body] = card(s ? "Edit service" : "Add a service", s ? s.name : "A service your agreements can include");
    const name = input(s?.name || "", {maxLength: 200}), desc = input(s?.description || "", {maxLength: 300}), cat = select(CATS, s?.category || "recurring");
    const scope = area(s?.scope || "", 6, {maxLength: 6000}), resp = area(s?.responsibilities || "", 4, {maxLength: 6000}), excl = area(s?.exclusions || "", 3, {maxLength: 6000});
    const row = el("div", "cat-row2"); row.append(field("Type", cat), field("Short description (shown in the builder)", desc));
    body.append(field("Service name (printed as the section heading)", name), row,
      field("Scope of the service", scope, "Each line that starts with a dash (-) prints as a bullet. This is the standard wording for this service; the person preparing an agreement can still add details."),
      field("Responsibilities", resp, "Who does what. Leave as it is to keep the standard wording."), field("Limitations and exclusions", excl));
    const models = el("div"); models.append(el("label", "", "Pricing models this service may use"));
    catalog.models.forEach(m => models.append(check(m.label, m.builtin ? "Standard model" : (m.wording || "Added by you"), (s?.models || []).includes(m.id), m.id)));
    models.append(el("p", "cat-note", "The person preparing an agreement can use one of these, or several when the client is to choose."));
    body.append(models);
    const on = toggle("Shown in the builder (untick to retire the service; agreements that already use it are not affected)", s ? s.active : true); body.append(on);
    const note = el("p", "cat-msg"); body.append(note);
    actions(c, button("Cancel", "button button-quiet", () => services(s?.id)), button(s ? "Save service" : "Add service", "button button-primary", async () => {
      try {
        const payload = {name: name.value, description: desc.value, category: cat.value, scope: scope.value, responsibilities: resp.value, exclusions: excl.value,
          models: [...models.querySelectorAll("input:checked")].map(i => i.value), active: on.querySelector("input").checked};
        const d = await api(s ? `/api/admin/catalog/services/${encodeURIComponent(s.id)}` : "/api/admin/catalog/services", payload);
        await services(d.id); window.tfscToast?.("Saved. New agreements use it from now on.");
      } catch (e) { msg(note, e.message); }
    }));
    box.replaceChildren(c);
  }
  function standardModel(m) {
    const [c, body] = card("Pricing model", m.label);
    body.append(el("p", "cat-note", "This is a standard pricing model: the system words it on the agreement and asks for its values in the builder. It cannot be changed here. To price a service another way, add a new pricing model."));
    $("cat-editor").replaceChildren(c);
  }
  function editModel(m) {
    const [c, body] = card(m ? "Edit pricing model" : "Add a pricing model", m ? m.label : "For example: Percentage of net collections");
    const label = input(m?.label || "", {maxLength: 80}), billing = select([["recurring", "Recurring, invoiced monthly"], ["one_time", "One-time"]], m?.billing || "recurring");
    const list = el("div");
    const addRow = f => {
      const r = el("div", "cat-field-row");
      const l = input(f?.label || "", {maxLength: 60, placeholder: "Field name, e.g. Percentage"}), k = select(Object.entries(KINDS), f?.kind || "amount");
      const req = toggle("Required", f ? f.required : true); const ch = input((f?.choices || []).join(", "), {placeholder: "Choices, separated by commas"});
      ch.hidden = k.value !== "choice"; k.addEventListener("change", () => { ch.hidden = k.value !== "choice"; });
      const del = button("Remove", "text-button", () => r.remove());
      l.setAttribute("aria-label", "Field name"); k.setAttribute("aria-label", "Kind of value");
      r.append(l, k, req, del, ch); list.append(r);
    };
    (m?.fields?.length ? m.fields : [{label: "Percentage", kind: "percent", required: true}]).forEach(addRow);
    const wording = area(m?.wording || "Client shall pay {Percentage}% of net collections received each month.", 3, {maxLength: 1500});
    const preview = el("p", "cat-note");
    body.append(field("Name", label), field("Billing", billing));
    const fl = el("div", "cat-f"); fl.append(el("label", "", "What the builder asks for"), list, button("+ Add a field", "text-button", () => addRow(null)),
      el("p", "cat-note", "Kinds: amount in USD, percent, number, date, short text, or a choice from a list.")); body.append(fl);
    body.append(field("Wording on the agreement", wording, "Write field names in braces, exactly as above: {Percentage}. A sentence about an optional field that is left empty is left out."), preview);
    const note = el("p", "cat-msg"); body.append(note);
    actions(c, button("Cancel", "button button-quiet", () => services()), button(m ? "Save pricing model" : "Add pricing model", "button button-primary", async () => {
      try {
        const fields = [...list.querySelectorAll(".cat-field-row")].map(r => { const [l, ch] = r.querySelectorAll('input[type="text"]'); return {label: l.value, kind: r.querySelector("select").value, required: r.querySelector('input[type="checkbox"]').checked, choices: ch.value.split(",").map(x => x.trim()).filter(Boolean)}; });
        const d = await api(m ? `/api/admin/catalog/models/${encodeURIComponent(m.id)}` : "/api/admin/catalog/models", {label: label.value, billing: billing.value, fields, wording: wording.value});
        msg(preview, "Example on the agreement: " + d.preview, true);
        tab = "models"; await services(d.id); window.tfscToast?.("Pricing model saved. Tick it on the services that may use it.");
      } catch (e) { msg(note, e.message); }
    }));
    $("cat-editor").replaceChildren(c);
  }

  // ---------------------------------------------------------------- Agreement types
  async function types(selected) {
    window.portalMark?.("#/admin/agreement-types");
    const [d, cfg] = await Promise.all([api("/api/admin/doc-types"), api("/api/config")]);
    const v = view(); v.replaceChildren();
    v.append(head("Agreement types", "The documents the builder can prepare. The three built-in agreements stay exactly as they are. A type you add uses the same letterhead, margins, footer and signature block, and appears in Create agreement as soon as it is switched on.", "+ Add an agreement type", () => editType(null, cfg)));
    const cols = el("div", "cat-cols"); const left = el("section", "cat-card"); const right = el("div"); right.id = "cat-editor"; cols.append(left, right); v.append(cols);
    const table = el("table", "cat-table"); const thead = el("thead"); const tr = el("tr"); ["Agreement type", "Kind", "Companies", "In the builder"].forEach(x => tr.append(el("th", "", x))); thead.append(tr);
    const tbody = el("tbody");
    d.builtin.forEach(t => {
      const row = el("tr"); const name = el("td"); name.append(el("span", "cat-sym", t.title[0]), el("b", "", t.title));
      const sw = toggle("", !t.hidden); sw.querySelector("input").setAttribute("aria-label", `Show ${t.title} in the builder`);
      sw.querySelector("input").addEventListener("change", async e => { try { await api(`/api/admin/doc-types/builtin/${t.key}`, {hidden: !e.target.checked}); window.tfscToast?.("Saved."); } catch (err) { e.target.checked = !e.target.checked; window.tfscToast?.(err.message); } });
      const on = el("td"); on.append(sw);
      row.append(name, el("td", "cat-lock", "🔒 Built in"), el("td", "", "All companies"), on); row.style.cursor = "default"; tbody.append(row);
    });
    d.custom.forEach(t => {
      const row = el("tr"); if (t.key === selected) row.className = "sel";
      const name = el("td"); name.append(el("span", "cat-sym", t.title[0]), el("b", "", t.title));
      row.append(name, el("td", "", "Added by you"), el("td", "", t.companies.length ? t.companies.map(n => (cfg.entities.find(e => e.name === n) || {}).short || n).join(", ") : "All companies"), el("td", t.active ? "cat-ok" : "", t.active ? "Shown" : "Hidden"));
      row.addEventListener("click", () => editType(t, cfg)); tbody.append(row);
    });
    table.append(thead, tbody); left.append(table);
    const foot = el("div", "cat-body"); foot.append(el("p", "cat-note", "🔒 Built-in agreements cannot be edited or deleted here, so nothing you rely on today can be changed by accident. Switch one off to hide it from the builder.")); left.append(foot);
    const pick = d.custom.find(t => t.key === selected) || d.custom[0];
    if (pick) editType(pick, cfg); else editType(null, cfg);
  }
  function editType(t, cfg) {
    const [c, body] = card(t ? "Agreement type" : "Add an agreement type", t ? t.title : "Your own agreement, on your letterheads");
    const title = input(t?.title || "", {maxLength: 120}), prefix = input(t?.prefix || "", {maxLength: 10}), desc = input(t?.description || "", {maxLength: 300});
    const row = el("div", "cat-row2"); row.append(field("Name (document title)", title), field("Short name for file names", prefix)); body.append(row, field("Description shown in the builder", desc));
    const file = el("input"); file.type = "file"; file.accept = ".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"; file.id = "cat-" + (++seq);
    const fileNote = t?.file_name ? `Current wording: ${t.file_name} · ${t.sections} sections. Choose a file only to replace it.` : "Headings become numbered sections. Places written like {Client name}, {Client address}, {Effective date}, {Governing state}, {Company name} or {Services and fees} are filled in from the builder; any other {Place} is asked for in the builder.";
    body.append(field("Wording (Word file, .docx)", file, fileNote));
    if (t && (t.known.length || t.fields.length)) {
      const f = el("div", "cat-f"); f.append(el("label", "", "Fill-in places found in the wording"));
      t.known.forEach(k => f.append(chip(k))); t.fields.forEach(k => f.append(chip(k.label + " · asked in the builder", "blue"))); body.append(f);
    }
    const comp = el("div", "cat-f"); comp.append(el("label", "", "Companies that may use it (none ticked: all companies)"));
    cfg.entities.forEach(e => comp.append(check(e.short || e.name, "", (t?.companies || []).includes(e.name), e.name)));
    body.append(comp);
    const services = toggle("Includes services & pricing (the builder shows the Services & fees step for it)", t?.services); const active = toggle("Shown in the builder", t ? t.active : true);
    body.append(services, el("br"), active);
    const note = el("p", "cat-msg"); body.append(note);
    const buttons = [button("Cancel", "button button-quiet", () => types(t?.key))];
    if (t) buttons.unshift(button("Preview as PDF", "button button-quiet", async () => {
      try {
        const r = await fetch(`/api/admin/doc-types/${t.key}/preview`, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: "{}"});
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "The preview could not be made.");
        const url = URL.createObjectURL(await r.blob()); window.open(url, "_blank"); setTimeout(() => URL.revokeObjectURL(url), 60000);
      } catch (e) { msg(note, e.message); }
    }));
    buttons.push(button(t ? "Save agreement type" : "Add agreement type", "button button-primary", async () => {
      try {
        const payload = {key: t?.key, title: title.value, prefix: prefix.value, description: desc.value, companies: [...comp.querySelectorAll("input:checked")].map(i => i.value),
          services: services.querySelector("input").checked, active: active.querySelector("input").checked};
        if (file.files[0]) {
          if (file.files[0].size > 10_000_000) throw new Error("The Word file must be smaller than 10 MB.");
          const raw = await new Promise((ok, bad) => { const rd = new FileReader(); rd.onload = () => ok(String(rd.result).split(",")[1]); rd.onerror = bad; rd.readAsDataURL(file.files[0]); });
          payload.file = raw; payload.file_name = file.files[0].name;
        }
        const d = await api("/api/admin/doc-types", payload);
        await types(d.key); window.tfscToast?.("Saved.");
      } catch (e) { msg(note, e.message); }
    }));
    actions(c, ...buttons);
    $("cat-editor").replaceChildren(c);
  }

  // ---------------------------------------------------------------- System check
  const STATE = {ok: ["Working", "cat-ok"], warn: ["Needs a look", "cat-warn"], bad: ["Not working", "cat-bad"]};
  async function system(fresh) {
    window.portalMark?.("#/admin/system");
    const d = await api("/api/system-status" + (fresh ? "?fresh=1" : ""));
    const v = view(); v.replaceChildren();
    v.append(head("System check", "Everything that works in the background: email, signed copies, document generation, the virus scanner, backups and disk space.", "Check again", () => system(true).catch(fail)));
    const problems = d.items.filter(i => i.state !== "ok");
    const [sc, sb] = card(problems.length ? `${problems.length} item${problems.length === 1 ? "" : "s"} need${problems.length === 1 ? "s" : ""} a look` : "Everything is working", `${d.items.length - problems.length} of ${d.items.length} checks passed · checked ${new Date(d.checked * 1000).toLocaleString([], {dateStyle: "medium", timeStyle: "short"})}`);
    sb.remove(); sc.style.marginBottom = "18px"; v.append(sc);
    const box = el("section", "cat-card"); const table = el("table", "cat-table"); const tr = el("tr"); ["Area", "Status", "Detail", ""].forEach(x => tr.append(el("th", "", x)));
    const thead = el("thead"); thead.append(tr); const tbody = el("tbody");
    [...problems, ...d.items.filter(i => i.state === "ok")].forEach(i => {
      const row = el("tr"); row.style.cursor = "default"; const area = el("td"); area.append(el("span", "cat-dot" + (i.state === "ok" ? "" : " w")), el("b", "", i.label));
      const link = el("td"); if (i.link && i.state !== "ok") { const a = el("a", "cat-link", "Open"); a.href = i.link; link.append(a); }
      row.append(area, el("td", STATE[i.state][1], STATE[i.state][0]), el("td", "", i.detail), link); tbody.append(row);
    });
    table.append(thead, tbody); box.append(table); v.append(box);
    flag(d.status);
  }
  function flag(status) {
    const b = document.querySelector('#nav-admin > button'); if (!b) return;
    b.querySelector(".nav-alert")?.remove();
    if (status === "bad") { const dot = el("span", "nav-alert"); dot.title = "Something in System check is not working"; dot.setAttribute("aria-label", "Something in System check is not working"); b.append(dot); }
  }
  window.tfscToast ||= text => { let t = document.querySelector(".toast"); if (!t) { t = el("div", "toast"); t.setAttribute("role", "status"); document.body.append(t); } t.textContent = text; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, 5000); };
  function fail(e) { const box = $("app-error"); if (box) { box.textContent = e.message; box.hidden = false; } }
  window.catalogShow = page => (page === "services" ? services() : page === "agreement-types" ? types() : system()).catch(fail);
  // The Admin menu shows a red dot when something is not working (Super Admin only).
  window.catalogReady = config => {
    if (config?.user?.role !== "super_admin") return;
    fetch("/api/system-status").then(r => r.ok ? r.json() : null).then(d => d && flag(d.status)).catch(() => {});
  };
})();
