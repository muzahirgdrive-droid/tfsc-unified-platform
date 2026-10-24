/* Clients: the client list and each client's profile (details, contacts, every document, envelopes, agreements, activity). */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const root = () => $("history-view");
  let config = null, user = null;
  const state = {q: "", page: 0, client: null, tab: "overview"};
  const ROLE_NAMES = {signatory: "Signatory", billing: "Billing", compliance: "Compliance", other: "Other"};
  const CATEGORIES = {contract: "Contract", correspondence: "Correspondence", identification: "Identification", insurance: "Insurance", other: "Other"};
  const STATUS = {draft: "Draft", sent: "Waiting for others", completing: "Finishing", completed: "Completed", declined: "Declined", voided: "Voided", expired: "Expired"};
  const GROUP_ORDER = ["Signed documents", "Certificates of Completion", "Sent for signature", "Attachments from signers", "Email attachments from the client", "Uploaded files", "Generated agreements"];

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function button(text, fn, cls = "button button-quiet") { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", () => Promise.resolve().then(fn).catch(fail)); return b; }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : ""; }
  function day(ts) { return ts ? new Date(ts * 1000).toLocaleDateString([], {dateStyle: "medium"}) : ""; }
  function size(n) { return !n ? "" : n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`; }
  function fail(e) { if (!e || !e.message) return; const box = $("app-error"); box.textContent = e.message; box.hidden = false; box.scrollIntoView({behavior: "smooth", block: "center"}); }
  function ok(text) { const box = $("status-message"); box.textContent = text; box.hidden = false; $("app-error").hidden = true; }
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
  function link(href, text, cls = "") { const a = el("a", cls, text); a.href = href; a.setAttribute("download", ""); return a; }
  function field(label, input) { const wrap = el("div", "field"); const l = el("label", "", label); input.id = input.id || `cl-${Math.random().toString(36).slice(2)}`; l.htmlFor = input.id; wrap.append(l, input); return wrap; }
  function input(value = "", type = "text", attrs = {}) { const i = el("input"); i.type = type; i.value = value ?? ""; Object.entries(attrs).forEach(([k, v]) => { i[k] = v; }); return i; }
  function select(options, value) { const s = el("select"); options.forEach(([v, t]) => s.append(new Option(t, v))); s.value = value ?? options[0]?.[0] ?? ""; return s; }
  function dialog(title, build, confirmText, onConfirm) {
    const box = el("dialog", "portal-dialog");
    const form = el("form"); form.method = "dialog";
    const head = el("div", "dialog-heading"); head.append(el("h2", "", title));
    const close = el("button", "icon-button", "×"); close.type = "button"; close.setAttribute("aria-label", "Close"); close.addEventListener("click", () => box.close());
    head.append(close);
    const message = el("p", "notice notice-danger"); message.hidden = true;
    const confirmButton = el("button", "button button-primary", confirmText); confirmButton.type = "submit";
    const body = el("div", "dialog-body"); build(body); body.append(message);
    const foot = el("div", "dialog-footer"); const cancel = el("button", "button button-quiet", "Cancel"); cancel.type = "button"; cancel.addEventListener("click", () => box.close());
    foot.append(cancel, confirmButton); form.append(body, foot);
    form.addEventListener("submit", async event => {
      event.preventDefault(); confirmButton.disabled = true; message.hidden = true;
      try { await onConfirm(form); box.close(); } catch (e) { message.textContent = e.message; message.hidden = false; } finally { confirmButton.disabled = false; }
    });
    box.append(head, form); document.body.append(box); box.addEventListener("close", () => box.remove()); box.showModal();
  }
  const readFile = file => new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.onerror = reject; r.readAsDataURL(file); });

  // ------------------------------------------------------------------ layout: list on the left, profile on the right
  async function show() {
    window.portalMark?.(state.client ? `#/clients/${state.client}` : "#/clients");
    const view = root(); view.replaceChildren();
    const head = el("div", "page-head");
    const title = el("div"); title.append(el("p", "eyebrow", "Your records"), el("h1", "", "Clients"),
      el("p", "page-sub", "Every client's details, contacts and documents in one place: generated agreements, everything sent for signature, signed copies with their certificates, and files."));
    const actions = el("div", "page-actions");
    if (user.role === "super_admin" && config.local) actions.append(button("Import existing local contracts", importLegacy));
    actions.append(button("New client", newClient, "button button-primary"));
    head.append(title, actions);
    view.append(head);
    const grid = el("div", "history-grid clients-grid");
    const side = el("div", "clients-side"); side.id = "clients-side";
    const detail = el("div", "history-detail clients-detail"); detail.id = "clients-detail";
    grid.append(side, detail); view.append(grid);
    await list();
    if (state.client) await profile(state.client);
    else detail.append(el("p", "empty-state", "Select a client to see their profile."));
  }

  let b0 = null;
  async function list() {
    b0 = null;
    const side = $("clients-side"); side.replaceChildren();
    const search = el("form", "clients-search");
    const q = input(state.q, "search", {placeholder: "Search clients"}); q.setAttribute("aria-label", "Search clients");
    search.append(q, el("button", "button button-quiet", "Search"));
    search.addEventListener("submit", event => { event.preventDefault(); state.q = q.value.trim(); state.page = 0; list().catch(fail); });
    side.append(search);
    const d = await api(`/api/clients?q=${encodeURIComponent(state.q)}&page=${state.page}`);
    const box = el("div", "client-list");
    d.clients.forEach(c => {
      const b = button("", () => { state.client = c.id; state.tab = "overview"; window.portalMark?.(`#/clients/${c.id}`); document.querySelectorAll(".client-entry").forEach(x => x.setAttribute("aria-selected", String(x === b))); return profile(c.id); }, "client-entry");
      b.setAttribute("aria-selected", String(c.id === state.client));
      b.append(el("strong", "", c.client_name), el("small", "", `${c.owner} · ${day(c.updated_at)}`));
      box.append(b);
    });
    if (!d.clients.length) box.append(el("p", "empty-state", state.q ? "No clients match." : "No clients yet. Create one, or generate an agreement."));
    else if (!state.client && window.matchMedia("(min-width: 1081px)").matches) { state.client = d.clients[0].id; b0 = box.firstChild; }
    side.append(box);
    const paging = el("div", "portal-paging");
    const back = button("Previous", () => { state.page--; return list(); }), more = button("Next", () => { state.page++; return list(); });
    back.disabled = state.page === 0; more.disabled = !d.has_more;
    paging.append(back, el("span", "", `Page ${state.page + 1}`), more); side.append(paging);
    if (b0) b0.setAttribute("aria-selected", "true");
    const jobs = el("details", "card clients-jobs"); jobs.style.padding = "12px 14px"; jobs.append(el("summary", "", "Recent agreement generations"));
    jobs.addEventListener("toggle", async () => {
      if (!jobs.open || jobs.dataset.loaded) return; jobs.dataset.loaded = "1";
      const j = await api("/api/jobs");
      jobs.append(...(j.jobs.length ? j.jobs.map(x => el("p", "muted small", `${when(x.created)} · ${x.state}${x.error ? ` · ${x.error}` : ""}`)) : [el("p", "muted small", "No recent generations.")]));
    });
    side.append(jobs);
  }

  // ------------------------------------------------------------------ profile
  async function profile(id) {
    const d = await api(`/api/clients/${id}/profile`);
    const c = d.client, box = $("clients-detail"); box.replaceChildren();
    const head = el("div", "clients-head");
    const name = el("div"); name.append(el("p", "eyebrow", "Client"), el("h3", "", c.name), el("p", "muted small", [c.address, c.entity && `Contracting company: ${c.entity}`, `Owner: ${c.owner}`].filter(Boolean).join(" · ")));
    const actions = el("div", "page-actions");
    if (d.can_edit) actions.append(button("Edit details", () => editClient(d)));
    actions.append(button("Create agreement", () => window.openClientDraft(id)));
    state.entity = c.entity && config.entities.some(e => e.name === c.entity) ? c.entity : config.entities[0]?.name;
    actions.append(button("Send documents for signature", () => sendFor(id), "button button-primary"));
    head.append(name, actions); box.append(head);
    const tabs = el("div", "tabs"); tabs.setAttribute("role", "tablist"); tabs.style.marginTop = "16px";
    const counts = d.counts, signed = counts.envelopes.completed || 0, waiting = (counts.envelopes.sent || 0) + (counts.envelopes.completing || 0);
    [["overview", "Overview"], ["contacts", `Contacts (${d.contacts.length})`], ["documents", "Documents"], ["signatures", `Signatures${waiting ? ` (${waiting} waiting)` : ""}`],
      ["agreements", `Agreements (${counts.contracts})`], ["activity", "Activity"]].forEach(([key, label]) => {
      const t = button(label, () => { state.tab = key; return profile(id); }, ""); t.setAttribute("role", "tab"); t.setAttribute("aria-selected", String(state.tab === key)); tabs.append(t);
    });
    box.append(tabs);
    const body = el("div", "clients-body"); box.append(body);
    if (state.tab === "overview") overview(body, d, signed, waiting);
    else if (state.tab === "contacts") contacts(body, d);
    else if (state.tab === "documents") await documents(body, id);
    else if (state.tab === "signatures") await signatures(body, id);
    else if (state.tab === "agreements") await agreements(body, id);
    else await activity(body, id);
  }

  function overview(body, d, signed, waiting) {
    const c = d.client, p = c.profile || {};
    const tiles = el("div", "kpi-row kpi-4"); tiles.style.margin = "4px 0 16px";
    [["Agreements generated", d.counts.contracts, "agreements"], ["Signed envelopes", signed, "signatures"], ["Waiting for signatures", waiting, "signatures"], ["Files on record", d.counts.files, "documents"]].forEach(([label, value, tab]) => {
      const t = el("button", "kpi"); t.type = "button"; t.style.textAlign = "left"; t.append(el("span", "", label), el("strong", "", value)); t.addEventListener("click", () => { state.tab = tab; profile(c.id).catch(fail); }); tiles.append(t);
    });
    body.append(tiles);
    const facts = el("dl", "clients-facts");
    [["Address", c.address], ["Phone", p.phone], ["Email", p.email], ["Website", p.website], ["Specialty", p.industry], ["NPI", p.npi], ["Tax ID", p.tax_id],
      ["Signatory on the last agreement", [c.signatory, c.signatory_title, c.signatory_email].filter(Boolean).join(" · ")], ["Client since", day(c.created)], ["Last updated", when(c.updated)]]
      .forEach(([k, v]) => { if (v) facts.append(el("dt", "", k), el("dd", "", v)); });
    body.append(facts);
    if (p.notes) { body.append(el("h4", "", "Notes")); body.append(el("p", "clients-notes", p.notes)); }
    const primary = d.contacts.filter(x => x.role === "signatory");
    if (primary.length) {
      body.append(el("h4", "", "Signatories"));
      primary.forEach(x => body.append(el("p", "muted", [x.name, x.title, x.email, x.phone].filter(Boolean).join(" · "))));
    }
  }

  function contacts(body, d) {
    body.append(button("Add contact", () => editContact(d.client.id), "button button-primary"));
    if (!d.contacts.length) { body.append(el("p", "empty-state", "No contacts yet. People you send envelopes to are added automatically.")); return; }
    const t = el("table", "portal-table"); const h = el("tr"); ["Name", "Role", "Email", "Phone", ""].forEach(x => h.append(el("th", "", x)));
    const th = el("thead"); th.append(h); t.append(th); const tb = el("tbody");
    d.contacts.forEach(x => {
      const tr = el("tr"); const who = el("td"); who.append(el("strong", "", x.name)); if (x.title) who.append(el("small", "", x.title)); if (x.notes) who.append(el("small", "", x.notes));
      const actions = el("td");
      actions.append(button("Edit", () => editContact(d.client.id, x)), button("Remove", async () => {
        if (!confirm(`Remove ${x.name} from this client's contacts?`)) return;
        await api(`/api/clients/${d.client.id}/contacts/${x.id}`, {delete: true}); await profile(d.client.id);
      }, "button button-danger"));
      tr.append(who, el("td", "", ROLE_NAMES[x.role] || x.role), el("td", "", x.email), el("td", "", x.phone), actions); tb.append(tr);
    });
    t.append(tb); const wrap = el("div"); wrap.style.overflow = "auto"; wrap.style.marginTop = "14px"; wrap.append(t); body.append(wrap);
  }

  async function documents(body, id) {
    const top = el("div", "page-actions");
    top.append(button("Upload a file", () => upload(id), "button button-primary"), button("Send documents for signature", () => sendFor(id)));
    body.append(top);
    const d = await api(`/api/clients/${id}/documents`);
    if (!d.documents.length) { body.append(el("p", "empty-state", "No documents yet.")); return; }
    const groups = {};
    d.documents.forEach(x => (groups[x.group] = groups[x.group] || []).push(x));
    GROUP_ORDER.filter(g => groups[g]).forEach(g => {
      body.append(el("h4", "clients-group", `${g} (${groups[g].length})`));
      const listBox = el("div", "clients-docs");
      groups[g].forEach(x => {
        const row = el("div", "clients-doc");
        const info = el("div"); info.append(el("strong", "", x.name));
        info.append(el("small", "", [x.kind, day(x.created), x.envelope && `Envelope ${x.envelope}`, x.status && x.group === "Sent for signature" && STATUS[x.status], x.pages && `${x.pages} page${x.pages === 1 ? "" : "s"}`,
          size(x.size), x.from && `from ${x.from}`, x.category && CATEGORIES[x.category], x.uploaded_by && `uploaded by ${x.uploaded_by}`,
          x.scan === "clean" ? "virus-scanned" : x.scan === "not_scanned" ? "not scanned" : ""].filter(Boolean).join(" · ")));
        if (x.note) info.append(el("small", "", x.note));
        const actions = el("div", "page-actions");
        actions.append(link(x.url, "Download", "button button-quiet button-sm"));
        if (x.envelope_id) actions.append(button("Open envelope", () => window.esignOpen(x.envelope_id), "button button-quiet button-sm"));
        if (x.can_send) actions.append(button("Send for signature", async () => { const env = await api(`/api/clients/files/${x.id}/send`, {}); window.esignOpenDraft(env); }, "button button-quiet button-sm"));
        if (x.deletable) actions.append(button("Remove", async () => { if (!confirm(`Remove ${x.name}?`)) return; await api(`/api/clients/files/${x.id}/delete`, {}); await profile(id); }, "button button-danger button-sm"));
        row.append(info, actions); listBox.append(row);
      });
      body.append(listBox);
    });
  }

  async function signatures(body, id) {
    const d = await api(`/api/clients/${id}/envelopes`);
    if (!d.envelopes.length) { body.append(el("p", "empty-state", "Nothing sent for signature yet.")); return; }
    const TONE = {draft: "muted", sent: "info", completing: "info", completed: "success", declined: "danger", voided: "muted", expired: "warning"};
    const t = el("table", "portal-table env-table"); const h = el("tr"); ["Subject", "Status", "Last change"].forEach(x => h.append(el("th", "", x)));
    const th = el("thead"); th.append(h); t.append(th); const tb = el("tbody");
    d.envelopes.forEach(e => {
      const tr = el("tr"); tr.tabIndex = 0; tr.addEventListener("click", () => window.esignOpen(e.id)); tr.addEventListener("keydown", ev => { if (ev.key === "Enter") window.esignOpen(e.id); });
      const subject = el("td"); const s2 = el("div", "env-subject"); s2.append(el("span", "doc-icon", "PDF")); const tx = el("div");
      tx.append(el("strong", "", e.subject || "(no subject)"), el("small", "", `To: ${e.recipients.filter(p => p.role !== "cc").map(p => p.name || p.email).join(", ")} · ${e.ref}`)); s2.append(tx); subject.append(s2);
      const status = el("td"); const cell = el("div", "status-cell"); cell.append(el("span", `chip chip-${TONE[e.status] || "neutral"}`, STATUS[e.status] || e.status));
      if (e.status === "sent") cell.append(el("small", "", `${e.progress[0]} of ${e.progress[1]} signed`));
      status.append(cell);
      tr.append(subject, status, el("td", "when-cell", e.completed_at ? `Completed ${day(e.completed_at)}` : e.sent_at ? `Sent ${day(e.sent_at)}` : `Updated ${day(e.updated)}`)); tb.append(tr);
    });
    t.append(tb); const wrap = el("div", "panel table-card"); const inner = el("div", "panel-body"); inner.append(t); wrap.append(inner); body.append(wrap);
  }

  async function agreements(body, id) {
    const d = await api(`/api/clients/${id}/history`);
    if (!d.contracts.length) { body.append(el("p", "empty-state", "No agreements generated yet. Use Create agreement.")); return; }
    d.contracts.forEach(c => {
      const card = el("article", "revision-card");
      card.append(el("strong", "", c.entity), el("p", "", c.id), el("p", "", `${when(c.created)} · ${c.owner}`));
      const links = el("div", "file-links");
      c.files.forEach(f => links.append(link(f.url, `${({baa: "BAA", services: "Services", nda: "NDA"}[f.agreement] || "Document")} · ${f.kind.toUpperCase()}`)));
      card.append(links);
      const sig = el("div", "file-links");
      (c.envelopes || []).forEach(e => sig.append(button(`Signature: ${(STATUS[e.status] || e.status).toLowerCase()} · ${e.ref}`, () => window.esignOpen(e.id), "text-button")));
      if (c.files.some(f => f.kind === "pdf")) sig.append(button("Send for signature", () => window.esignFromContract(c.id), "text-button"));
      card.append(sig); body.append(card);
    });
  }

  // Everything that happened with this client: who generated, opened, sent, viewed, signed and downloaded what, and when.
  async function activity(body, id, page = 0) {
    const d = await api(`/api/clients/${id}/activity?page=${page}`);
    if (!d.activity.length && !page) { body.append(el("p", "empty-state", "No activity recorded.")); return; }
    body.append(el("p", "muted small", "Portal users and signers. Times are shown in your time zone."));
    d.activity.forEach(a => {
      const row = el("div", `activity-entry ${a.type}`);
      row.append(el("strong", "", a.action), el("small", "", [a.who, a.type === "signer" ? "signer" : "", when(a.at), a.ip && `IP ${a.ip}`, a.browser].filter(Boolean).join(" · ")));
      if (a.detail) row.append(el("small", "", a.detail));
      body.append(row);
    });
    if (d.has_more) { const more = button("Show older activity", async () => { more.remove(); await activity(body, id, page + 1); }); body.append(more); }
  }
  function sendFor(id) {
    return window.esignNewForClient(id, state.entity || config.entities[0]?.name);
  }

  // ------------------------------------------------------------------ dialogs
  function profileFields(form, c = {}, p = {}) {
    form.append(field("Client legal name", input(c.name || "", "text", {maxLength: 200, required: true, name: "name"})),
      field("Address", input(c.address || "", "text", {maxLength: 500, name: "address"})));
    const two = el("div", "settings-grid"); two.append(field("Phone", input(p.phone || "", "tel", {maxLength: 40, name: "phone"})), field("Email", input(p.email || "", "email", {maxLength: 200, name: "email"})));
    const three = el("div", "settings-grid"); three.append(field("Website", input(p.website || "", "text", {maxLength: 200, name: "website"})), field("Specialty", input(p.industry || "", "text", {maxLength: 120, name: "industry"})));
    const four = el("div", "settings-grid"); four.append(field("NPI", input(p.npi || "", "text", {maxLength: 10, name: "npi", inputMode: "numeric"})), field("Tax ID", input(p.tax_id || "", "text", {maxLength: 20, name: "tax_id"})));
    form.append(two, three, four, field("Notes", Object.assign(el("textarea"), {value: p.notes || "", maxLength: 4000, rows: 4, name: "notes"})));
  }
  const values = form => Object.fromEntries([...form.querySelectorAll("[name]")].map(i => [i.name, i.value.trim()]));

  function newClient() {
    dialog("New client", form => {
      form.append(field("Contracting company", select(config.entities.map(e => [e.name, e.name]))));
      form.querySelector("select").name = "entity";
      profileFields(form);
    }, "Create client", async form => {
      const d = await api("/api/clients/new", values(form));
      state.client = d.id; state.tab = "overview"; ok("Client created."); await show();
    });
  }

  function editClient(d) {
    dialog("Edit client details", form => profileFields(form, d.client, d.client.profile || {}), "Save", async form => {
      await api(`/api/clients/${d.client.id}/profile`, values(form)); ok("Client details saved."); await show();
    });
  }

  function editContact(clientId, contact) {
    dialog(contact ? "Edit contact" : "Add contact", form => {
      form.append(field("Name", input(contact?.name || "", "text", {maxLength: 150, required: true, name: "name"})), field("Title", input(contact?.title || "", "text", {maxLength: 150, name: "title"})));
      const two = el("div", "settings-grid"); two.append(field("Email", input(contact?.email || "", "email", {maxLength: 200, name: "email"})), field("Phone", input(contact?.phone || "", "tel", {maxLength: 40, name: "phone"})));
      const role = select(Object.entries(ROLE_NAMES), contact?.role || "signatory"); role.name = "role";
      form.append(two, field("Role", role), field("Notes", Object.assign(el("textarea"), {value: contact?.notes || "", maxLength: 2000, rows: 3, name: "notes"})));
    }, "Save contact", async form => {
      await api(contact ? `/api/clients/${clientId}/contacts/${contact.id}` : `/api/clients/${clientId}/contacts`, values(form));
      state.tab = "contacts"; await profile(clientId);
    });
  }

  function upload(clientId) {
    dialog("Upload a file", form => {
      form.append(el("p", "portal-note", "PDF, Word, Excel, CSV, text, PNG or JPEG, up to 15 MB. Files are checked for viruses and kept with the client's records."));
      form.append(field("File", input("", "file", {required: true, name: "file", accept: ".pdf,.docx,.doc,.xlsx,.xls,.csv,.txt,.png,.jpg,.jpeg,.odt,.rtf"})));
      const category = select(Object.entries(CATEGORIES), "other"); category.name = "category";
      form.append(field("Category", category), field("Note (optional)", input("", "text", {maxLength: 1000, name: "note"})));
    }, "Upload", async form => {
      const file = form.querySelector('[name="file"]').files[0];
      if (!file) throw new Error("Choose a file.");
      if (file.size > 15000000) throw new Error("Choose a file smaller than 15 MB.");
      const d = await api(`/api/clients/${clientId}/files`, {name: file.name, data: await readFile(file), category: form.querySelector('[name="category"]').value, note: form.querySelector('[name="note"]').value});
      ok(`${d.name} uploaded${d.scan === "clean" ? " and checked for viruses" : ""}.`);
      state.tab = "documents"; await profile(clientId);
    });
  }

  async function importLegacy() {
    const d = await api("/api/import-legacy", {});
    ok(`Imported ${d.clients} clients and ${d.contracts} contracts (${d.skipped} skipped).`); await show();
  }

  window.clientsReady = c => { config = c; user = c.user; };
  window.clientsShow = id => { if (id) { state.client = id; state.tab = "overview"; } return show().catch(fail); };
})();
