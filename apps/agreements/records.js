/* Client records: the clients register (#/clients) with bulk actions, the Trash (#/clients/trash), the client record's agreements
   and "Signed agreements by company", "Add a past signed agreement", "Import previous clients" (#/clients/import) and the Signed
   agreements register (#/clients/baa-register: BAAs, Services Agreements and Mutual NDAs).
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
    if (r.status === 401) { window.tfscSignIn("expired"); throw new Error("Please sign in again."); }
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
  const reg = {q: "", company: "", source: "", baa: "all", show: "all", test: "real", page: 0};
  const picked = new Map();          // client id -> {name, client_number}: the clients ticked for a bulk action
  let regTimer = null;
  const STATE = {signed: ["ok", "✓", "Signed"], out: ["out", "◷", "Out for signature"], none: ["none", "–", "Not signed"]};
  function agreementState(a, note) {
    const [cls, icon, text] = STATE[a?.state || "none"];
    const box = el("div", `rc-st ${cls}`); const b = el("b"); b.append(el("i", "", icon), document.createTextNode(text)); box.append(b);
    if (a?.state === "signed") box.append(el("small", "", day(a.date) || "Date not recorded"));
    if (a?.state === "out" && a.sent) box.append(el("small", "", `Sent ${day(a.sent)}`));
    if (note) box.append(el("small", "warn", note));
    return box;
  }
  function tick(checked, partial, label, onChange) {
    const c = el("input"); c.type = "checkbox"; c.className = "rc-check"; c.checked = checked; c.indeterminate = !!partial; c.setAttribute("aria-label", label);
    c.addEventListener("click", e => e.stopPropagation()); c.addEventListener("change", () => onChange(c.checked)); return c;
  }

  // One handler closes any open "More actions" menu when the person clicks elsewhere.
  document.addEventListener("click", e => document.querySelectorAll(".rc-more .rc-menu").forEach(m => { if (!m.hidden && !m.parentElement.contains(e.target)) m.hidden = true; }));

  // Bulk actions on the ticked clients. Add an entry here to offer another action; each is checked on the server as the single one is.
  const BULK = [
    {label: "Mark as test", run: ids => api("/api/client-records/mark", {ids, test: true}).then(r => `${plural(r.changed, "client")} marked as test.`)},
    {label: "Mark as real", run: ids => api("/api/client-records/mark", {ids, test: false}).then(r => `${plural(r.changed, "client")} marked as real.`)},
    {label: "Export (.xlsx)", run: ids => exportClients(ids, "xlsx")},
    {label: "Export (.csv)", run: ids => exportClients(ids, "csv"), more: true},
    {label: "Move to Trash", danger: true, superAdmin: true, run: (ids, ctx) => confirmDelete(ids, ctx)},
  ];
  async function exportClients(ids, format) {
    const r = await fetch("/api/client-records/export", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify({ids, format})});
    if (!r.ok) { let d = {}; try { d = await r.json(); } catch (_) { /* empty */ } throw new Error(d.error || "The export failed."); }
    const blob = await r.blob(); const a = el("a"); a.href = URL.createObjectURL(blob);
    a.download = (/filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "") || [])[1] || `Clients.${format}`; document.body.append(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    return `${plural(ids.length, "client")} exported.`;
  }
  function bulkBar(ctx) {
    document.querySelector(".rc-bulk")?.remove();
    if (!picked.size) return;
    const bar = el("div", "rc-bulk"); bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Bulk actions");
    const count = el("span", "count"); count.append(el("em", "", picked.size), document.createTextNode(picked.size === 1 ? "client selected" : "clients selected"));
    bar.append(count);
    const run = async action => {
      const ids = [...picked.keys()];
      const message = await action.run(ids, ctx);
      if (message) { if (action.label.startsWith("Mark")) picked.clear(); ok(message); await ctx.refresh(); }
    };
    const visible = BULK.filter(x => !x.superAdmin || ctx.user.role === "super_admin");
    visible.filter(x => !x.more && !x.danger).forEach(x => bar.append(button(x.label, () => run(x), "")));
    const extra = visible.filter(x => x.more);
    if (extra.length) {
      const wrap = el("span", "rc-more"); const toggle = button("More actions ▾", () => { menu.hidden = !menu.hidden; }, "");
      const menu = el("div", "rc-menu up"); menu.hidden = true; extra.forEach(x => menu.append(button(x.label, () => { menu.hidden = true; return run(x); }, "")));
      wrap.append(toggle, menu); bar.append(wrap);
    }
    const danger = visible.filter(x => x.danger);
    if (danger.length) { bar.append(el("span", "sep")); danger.forEach(x => bar.append(button(x.label, () => run(x), "danger"))); }
    const clear = button("×", () => { picked.clear(); return ctx.refresh(); }, "x"); clear.setAttribute("aria-label", "Clear the selection"); bar.append(clear);
    document.body.append(bar);
  }

  // Delete clients (Super Admin): a confirmation that names them, then the Trash for 30 days.
  async function confirmDelete(ids, ctx, after) {
    const d = await api(`/api/client-records/delete-preview?ids=${encodeURIComponent(ids.join(","))}`);
    const n = d.clients.length;
    return new Promise(resolve => {
      const box = el("dialog", "rc-dialog rc-confirm"); box.setAttribute("aria-labelledby", "rc-del-title");
      const top = el("div", "rc-confirm-top"); const ic = el("span", "rc-confirm-ic", "🗑"); ic.setAttribute("aria-hidden", "true");
      const t = el("div"); const h = el("h2", "", n === 1 ? "Move this client to the Trash?" : `Move ${n} clients to the Trash?`); h.id = "rc-del-title";
      t.append(h, el("p", "", n === 1 ? `${d.clients[0].client_number} ${d.clients[0].name} goes to the Trash and can be restored for 30 days.` : "The selected clients go to the Trash and can be restored for 30 days."));
      top.append(ic, t);
      const body = el("div", "rc-confirm-body");
      if (n > 1 || d.clients[0].companies.length) {
        const list = el("ul", "rc-confirm-list");
        d.clients.forEach(c => { const li = el("li"); li.append(num(c.client_number), el("span", "", c.name), el("small", "", c.companies.join(", "))); list.append(li); });
        body.append(list);
      }
      const until = new Date(d.restorable_until * 1000).toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"});
      const info = el("div", "rc-note info"); const it = el("div");
      it.append(el("b", "", n === 1 ? `It moves to the Trash for ${d.days} days.` : `They move to the Trash for ${d.days} days.`),
        document.createTextNode(` A Super Admin can restore ${n === 1 ? "it" : "them"} until ${until}, exactly as now and with the same client number${n === 1 ? "" : "s"}. After that ${n === 1 ? "it is" : "they are"} deleted permanently; signed agreements and certificates are kept. Client numbers are never given to another client.`));
      info.append(el("span", "", "ⓘ"), it); body.append(info);
      d.clients.filter(c => c.waiting.length).forEach(c => {
        const w = el("div", "rc-note warn"); const wt = el("div");
        wt.append(el("b", "", `${c.name} has ${plural(c.waiting.length, "envelope")} out for signature.`),
          document.createTextNode(" The signing links keep working; when signed, the signed copy is filed to the client and is there if you restore it."));
        w.append(el("span", "", "⚠"), wt); body.append(w);
      });
      const foot = el("div", "rc-confirm-foot"); foot.append(el("small", "", "Only Super Admins can delete clients. Recorded in the activity log."));
      const cancel = button("Cancel", () => { box.close(); }, "button button-quiet");
      const go = button(n === 1 ? "Delete client" : `Delete ${n} clients`, async () => {
        go.disabled = true;
        try {
          const r = await api("/api/client-records/delete", {ids: d.clients.map(c => c.id)});
          box.close(); d.clients.forEach(c => picked.delete(c.id));
          ok(`${n === 1 ? `${d.clients[0].client_number} ${d.clients[0].name} was` : `${plural(r.deleted, "client")} were`} moved to the Trash. Restore from Clients › Trash within ${d.days} days.`);
          if (after) await after(); else if (ctx) await ctx.refresh();
          resolve(null);
        } catch (e) { go.disabled = false; fail(e); }
      }, "button button-danger");
      foot.append(cancel, go); box.append(top, body, foot);
      box.addEventListener("close", () => { box.remove(); resolve(null); });
      document.body.append(box); box.showModal(); cancel.focus();
    });
  }

  async function register(view, ctx) {
    const qs = new URLSearchParams({q: reg.q, company: reg.company, source: reg.source, baa: reg.baa, show: reg.show, test: reg.test, page: reg.page});
    const d = await api(`/api/client-records/register?${qs}`);
    reg.page = d.page;
    const wrap = el("div", "rc-wrap");
    const k = d.kpis;
    const trash = d.can_delete ? button("Trash", () => window.portalRoute("#/clients/trash"), "button button-quiet rc-trash-button") : null;
    if (trash) trash.append(el("em", "", d.trash));
    wrap.append(head("Your records", "Clients", "Every client in one register, each with a permanent client number. Agreements signed in this portal, in DocuSign, by email or through Jotform are all kept on the client's record.", [
      ctx.user.role === "super_admin" && ctx.config.local ? button("Import existing local contracts", ctx.importLegacy) : null,
      d.can_import ? button("Import previous clients", () => window.portalRoute("#/clients/import")) : null,
      button("Signed agreements register", () => window.portalRoute("#/clients/baa-register")), trash,
      button("New client", ctx.newClient, "button button-primary")]));
    const kpis = el("div", "rc-kpis");
    kpis.append(kpi("Clients", k.clients, `${k.portal_only} portal only · ${k.earlier} with earlier records`),
      kpi("Signed BAAs", k.covered, `${k.coverage} % of clients · ${k.missing} missing`, "good"),
      kpi("Signed Services Agreements", k.services_signed, `${k.services_coverage} % of clients · ${k.clients - k.services_signed} without one`, "good"),
      kpi("Out for signature", k.waiting, k.waiting ? `${k.waiting_names.join(" · ")}${k.waiting > k.waiting_names.length ? " and more" : ""} waiting` : "Nothing waiting", k.waiting ? "warn" : ""));
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
    filters.append(q, company, source, test, seg([["all", "All", d.counts.all], ["both", "BAA + Services signed", d.counts.both], ["waiting", "Waiting", d.counts.waiting],
      ["no_baa", "No BAA", d.counts.no_baa], ["no_services", "No Services", d.counts.no_services]], reg.show,
      key => { reg.show = key; reg.baa = "all"; reg.page = 0; ctx.refresh(); }, "Signed agreements"));
    panel.append(filters);
    const body = el("div", "panel-body");
    if (!d.clients.length) body.append(el("p", "rc-empty", reg.q || reg.company || reg.source || reg.show !== "all" ? "No clients match these filters." : "No clients yet. Create one, generate an agreement or import previous clients."));
    else {
      const [t, tb] = table(["", "Client no.", "Client", "Companies", "BAA", "Services Agreement", "NDA", "Signed through", "Last activity"]);
      const onPage = d.clients.map(c => c.id), chosen = onPage.filter(id => picked.has(id)).length;
      const headCell = t.querySelector("thead th"); headCell.className = "rc-c";
      headCell.append(tick(chosen === onPage.length, chosen > 0 && chosen < onPage.length, "Select every client on this page", on => {
        d.clients.forEach(c => on ? picked.set(c.id, {name: c.name, client_number: c.client_number}) : picked.delete(c.id)); register(view, ctx).catch(fail);
      }));
      d.clients.forEach(c => {
        const tr = el("tr"); if (c.test) tr.classList.add("row-test"); if (picked.has(c.id)) tr.classList.add("rc-picked");
        const box = el("td", "rc-c"); box.append(tick(picked.has(c.id), false, `Select ${c.name}`, on => {
          if (on) picked.set(c.id, {name: c.name, client_number: c.client_number}); else picked.delete(c.id);
          tr.classList.toggle("rc-picked", on); bulkBar(ctx);
          const all = t.querySelector("thead .rc-check"), n = onPage.filter(id => picked.has(id)).length; all.checked = n === onPage.length; all.indeterminate = n > 0 && n < onPage.length;
        }));
        const who = cell(strongSmall(c.name, [c.place, c.npi && `NPI ${c.npi}`].filter(Boolean).join(" · ")));
        if (c.test) who.querySelector("strong").append(testBadge("Test client"));
        const companies = cell(); c.companies.forEach(x => companies.append(co(x, !x.linked)));
        if (!c.companies.length) companies.append(el("span", "muted", "—"));
        const ag = c.agreements || {};
        const baaNote = c.baa_missing_for.length ? `Missing for ${c.baa_missing_for.join(", ")}` : "";
        const last = el("td", "d", dayTs(c.last_activity));
        tr.append(box, cell(num(c.client_number)), who, companies, cell(agreementState(ag.baa, baaNote)), cell(agreementState(ag.services)), cell(agreementState(ag.nda)),
          cell(c.through ? src(c.through) : el("span", "muted", "—")), last);
        rowLink(tr, () => ctx.openClient(c.id)); tb.append(tr);
      });
      body.append(t);
      const legend = el("div", "rc-legend");
      [["ok", "Signed (date signed)"], ["out", "Out for signature (date sent)"], ["none", "Not signed"]].forEach(([cls, text]) => { const s = el("span"); s.append(el("i", cls), document.createTextNode(text)); legend.append(s); });
      legend.append(el("span", "rc-legend-note", "A client who works with two companies shows the BAA still missing for either one."));
      body.append(legend);
    }
    panel.append(body);
    const pager = el("div", "rc-pager");
    const from = d.total ? d.page * d.page_size + 1 : 0, to = Math.min(d.total, (d.page + 1) * d.page_size);
    const nav = el("span"); const prev = button("Previous", () => { reg.page--; return ctx.refresh(); }, "button button-quiet button-sm"); prev.disabled = d.page === 0;
    const next = button("Next", () => { reg.page++; return ctx.refresh(); }, "button button-quiet button-sm"); next.disabled = to >= d.total; nav.append(prev, next);
    pager.append(el("span", "", `${from}–${to} of ${plural(d.total, "client")} · numbers are permanent and never reused`), nav);
    panel.append(pager);
    wrap.append(panel);
    view.replaceChildren(wrap);
    bulkBar(ctx);
    return {focusSearch: () => { const n = view.querySelector('input[type="search"]'); if (n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }};
  }

  // ------------------------------------------------------------------ the Trash (Super Admin)
  const trashPicked = new Set();
  async function trashPage(view) {
    const d = await api("/api/client-records/trash");
    const wrap = el("div", "rc-wrap");
    const restoreSome = async ids => {
      const r = await api("/api/client-records/restore", {ids});
      ids.forEach(id => trashPicked.delete(id));
      ok(r.restored === 1 ? `Restored ${r.clients[0].client_number} ${r.clients[0].name} with its original client number. Its agreements, signed copies and history are all back.`
        : `Restored ${plural(r.restored, "client")} with their original client numbers.`);
      await trashPage(view);
    };
    const restoreSel = button(trashPicked.size ? `Restore selected (${trashPicked.size})` : "Restore selected", () => restoreSome([...trashPicked]), "button button-primary");
    restoreSel.disabled = !trashPicked.size;
    wrap.append(head("Clients", "Trash", `Deleted clients stay here for ${d.days} days. Restore one and it goes back to the Clients register exactly as it was, with its original client number. After ${d.days} days it is deleted permanently, and its client number is retired: never given to anyone else.`,
      [button("← All clients", () => window.portalRoute("#/clients")), restoreSel]));
    const kpis = el("div", "rc-kpis");
    const next = d.next_purge;
    kpis.append(kpi("In the Trash", d.clients.length, "Deleted by a Super Admin"),
      kpi("Deleted permanently next", next ? new Date(next.purge_at * 1000).toLocaleDateString("en-US", {month: "short", day: "numeric"}) : "—", next ? `${next.client_number} ${next.name}` : "Nothing waiting", next && next.days_left <= 7 ? "warn" : ""),
      kpi("Restored this month", d.restored_this_month, "With their original numbers"), kpi("Retired client numbers", d.retired_numbers, "Never reused"));
    wrap.append(kpis);
    const panel = el("section", "panel table-card"); const body = el("div", "panel-body");
    if (!d.clients.length) body.append(el("p", "rc-empty", "The Trash is empty."));
    else {
      const [t, tb] = table(["", "Client no.", "Client", "Companies", "Deleted", "Deleted by", "Restorable for", ""]);
      const ids = d.clients.map(c => c.id), chosen = ids.filter(id => trashPicked.has(id)).length;
      const hc = t.querySelector("thead th"); hc.className = "rc-c";
      hc.append(tick(chosen === ids.length, chosen > 0 && chosen < ids.length, "Select every client in the Trash", on => { ids.forEach(id => on ? trashPicked.add(id) : trashPicked.delete(id)); trashPage(view).catch(fail); }));
      d.clients.forEach(c => {
        const tr = el("tr"); if (trashPicked.has(c.id)) tr.classList.add("rc-picked");
        const box = el("td", "rc-c"); box.append(tick(trashPicked.has(c.id), false, `Select ${c.name}`, on => { if (on) trashPicked.add(c.id); else trashPicked.delete(c.id); trashPage(view).catch(fail); }));
        const left = el("div", `rc-days${c.days_left <= 7 ? " low" : ""}`); const bar = el("span", "rc-bar"); const fill = el("i"); fill.style.width = `${Math.max(3, c.days_left / d.days * 100)}%`; bar.append(fill);
        left.append(el("b", "", plural(c.days_left, "day") + " left"), bar, el("small", "", `Deleted permanently on ${new Date(c.purge_at * 1000).toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"})}`));
        const companies = cell(); c.companies.forEach(x => companies.append(el("span", "rc-co plain", x))); if (!c.companies.length) companies.append(el("span", "muted", "—"));
        tr.append(box, cell(num(c.client_number)), cell(strongSmall(c.name, place(c.address))), companies, el("td", "d", dayTs(c.deleted_at)), el("td", "", c.deleted_by || "—"),
          cell(left), cell(button("Restore", () => restoreSome([c.id]), "button button-quiet button-sm")));
        tb.append(tr);
      });
      body.append(t);
    }
    const note = el("div", "rc-note info"); const nt = el("div");
    nt.append(el("b", "", "What permanent deletion removes: "), document.createTextNode("the client's record, contacts, notes, saved form and the files the team uploaded to it. "),
      el("b", "", "What is kept: "), document.createTextNode("signed agreements and their Certificates of Completion, and agreements signed before this portal, because they are legal records (HIPAA asks for six years); they stay findable in Agreements by envelope ID. The activity log keeps who deleted and restored what. The client number stays retired."));
    note.append(el("span", "", "ⓘ"), nt); body.append(note);
    panel.append(body); wrap.append(panel); view.replaceChildren(wrap);
  }
  function place(address) { const parts = (address || "").split(",").map(x => x.trim()).filter(Boolean); return parts.length > 1 ? parts.slice(-2).join(", ").replace(/\s+\d{5}(-\d{4})?$/, "") : (parts[0] || ""); }

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
      if (x.status !== "signed" && (x.envelope_id || x.contract_id))
        files.append(button("Follow up", () => window.tfscFollowups?.open(x.envelope_id ? {envelope_id: x.envelope_id, title: x.title || "Agreement", onSaved: ctx.reload} : {contract_id: x.contract_id, title: x.title || "Agreement", onSaved: ctx.reload}), "text-button"));
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
    aside.append(el("h3", "", "Signed agreements by company"));
    d.companies.forEach(c => {
      const row = el("div", "rc-cov"); const left = el("div"); left.append(co(c));
      const line = (label, signed, out) => {
        const s = el("small", signed ? "rc-line ok" : out ? "rc-line wait" : "rc-line");
        s.textContent = signed ? `✓ ${label} · ${[SOURCES[signed.source], day(signed.date)].filter(Boolean).join(" · ")}` : out ? `◷ ${label} · out for signature` : `– ${label} · not signed`;
        return s;
      };
      if (c.linked || c.baa || c.services) {
        left.append(line("BAA", c.baa, c.out), line("Services Agreement", c.services, c.services_out));
        if (c.baa) row.append(left, el("span", "ok", "✓ On file"));
        else if (c.out) row.append(left, el("span", "wait", "Waiting"));
        else row.append(left, button("Send a BAA", () => sendBaa(client.id, c.name), "button button-quiet button-sm"));
      } else { left.append(el("small", "", "No agreement with this company")); row.append(left, el("span", "muted", "—")); }
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
      note.textContent = [`Saved to ${client.client_number || "the client's record"}`, ["baa", "services", "nda"].includes(kind.value) ? "shows in the Signed agreements register" : "",
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
    const a = el("div", "rc-actions"); a.append(button("Open the clients register", () => window.portalRoute("#/clients"), "button button-primary"), button("Signed agreements register", () => window.portalRoute("#/clients/baa-register")));
    panel.append(a); return panel;
  }

  // ------------------------------------------------------------------ the Signed agreements register (BAA, Services Agreement, Mutual NDA)
  const baa = {kind: "baa", company: "", q: "", status: "all", page: 0};
  let baaTimer = null;
  async function baaRegister(view) {
    const qs = new URLSearchParams({kind: baa.kind, company: baa.company, q: baa.q, status: baa.status, page: baa.page});
    const d = await api(`/api/client-records/baa-register?${qs}`);
    const short = d.short, plural_ = {baa: "BAAs", services: "Services Agreements", nda: "Mutual NDAs"}[d.kind];
    baa.company = d.company; baa.page = d.page;
    const wrap = el("div", "rc-wrap");
    const tab = d.tabs.find(t => t.name === d.company) || {short: "", signed: 0, total: 0};
    const exportUrl = format => `/api/client-records/baa-register/export?${new URLSearchParams({kind: d.kind, company: d.company, format, q: baa.q, status: baa.status})}`;
    const xlsx = link(exportUrl("xlsx"), "Export register (.xlsx)"); xlsx.className = "button button-quiet";
    const csv = link(exportUrl("csv"), "Export (.csv)"); csv.className = "button button-quiet";
    const zip = link(`/api/client-records/baa-register/files.zip?${new URLSearchParams({kind: d.kind, company: d.company})}`, `Download all signed ${plural_} (.zip)`); zip.className = "button button-primary";
    if (!tab.signed) { zip.removeAttribute("href"); zip.setAttribute("aria-disabled", "true"); zip.classList.add("is-disabled"); zip.title = `No signed ${short} for this company yet`; }
    if (!d.company) [xlsx, csv].forEach(a => a.removeAttribute("href"));
    wrap.append(head("Clients", "Signed agreements register", "Every signed agreement for every company and client, wherever it was signed: Business Associate Agreements, Services Agreements and Mutual NDAs. Choose the agreement, then the company. Export it for an audit or download the signed copies at once.",
      [button("← All clients", () => window.portalRoute("#/clients")), xlsx, csv, zip]));
    const k = d.kpis; const kpis = el("div", "rc-kpis");
    const by = Object.entries(k.earlier_by_source).filter(([, n]) => n).map(([s, n]) => `${SOURCES[s]} ${n}`).join(" · ");
    kpis.append(kpi(`${short} coverage`, `${k.coverage} %`, `${k.covered} of ${plural(k.clients, "client")}`, "good"), kpi("Signed in this portal", k.portal, "with Certificates of Completion"),
      kpi("From earlier systems", k.earlier, by || "DocuSign, manual (email) and Jotform"), kpi("Not signed yet", k.missing, "clients listed under \"Not signed\"", k.missing ? "warn" : ""));
    wrap.append(kpis);
    const panel = el("section", "panel table-card");
    const types = el("div", "rc-types"); types.setAttribute("role", "group"); types.setAttribute("aria-label", "Agreement");
    Object.entries(d.types).forEach(([key, t]) => {
      const b = el("button", "rc-type"); b.type = "button"; b.setAttribute("aria-pressed", String(key === d.kind));
      b.append(document.createTextNode({baa: "Business Associate Agreements", services: "Services Agreements", nda: "Mutual NDAs"}[key]),
        el("em", "", t.out ? `${t.signed} of ${t.total} · ${t.out} out` : `${t.signed} of ${t.total}`));
      b.addEventListener("click", () => { baa.kind = key; baa.page = 0; baa.status = "all"; baaRegister(view).catch(fail); }); types.append(b);
    });
    panel.append(types);
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
      const [t, tb] = table(["Client no.", "Client", `${short} signed`, "Signed through", "Signed by", "Effective", "File"]);
      d.rows.forEach(r => {
        const tr = el("tr");
        const signedCell = el("td", "d");
        if (r.signed) signedCell.append(el("span", "rc-baa", day(r.date) || "Date not recorded"));
        else if (r.out) signedCell.append(el("span", "rc-baa out", "Out for signature"), ...(r.sent ? [el("small", "rc-sent", `Sent ${day(r.sent)}`)] : []));
        else signedCell.append(el("span", "rc-baa miss", "Not signed"));
        const through = cell(r.signed ? src(r.source) : d.kind === "baa" ? button("Send a BAA", () => sendBaa(r.id, d.company), "button button-quiet button-sm") : el("span", "muted", "—"));
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

  window.Records = {register, agreementsTable, sidePanel, addPast, importPage, baaRegister, trashPage, confirmDelete, exportClients, num, co, src, day, config: null,
    resetRegister: () => Object.assign(reg, {q: "", company: "", source: "", baa: "all", show: "all", test: "real", page: 0})};
})();
