/* Electronic signature for senders: Agreements (folders and list), envelope details, the send wizard, Add fields, company mailboxes and bulk send. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const root = () => $("esign-view");
  const COLORS = ["#f5b400", "#2a8ff0", "#e5547a", "#2fae7f", "#8e6cf0", "#f0863c", "#3bb6c4", "#9a9a9a"];
  const STATUS = {draft: "Draft", sent: "Waiting for others", completing: "Finishing", completed: "Completed", declined: "Declined", voided: "Voided", expired: "Expired", template: "Template"};
  const TONE = {draft: "muted", sent: "info", completing: "info", completed: "success", declined: "danger", voided: "muted", expired: "warning", template: "neutral"};
  const R_STATUS = {created: "Waiting for their turn", sent: "Sent", delivered: "Viewed", signed: "Signed", completed: "Completed", declined: "Declined", auth_failed: "Locked: wrong access code",
    reassigned: "Assigned to someone else"};
  // DocuSign's recipient actions, with an icon and what each one means.
  const ROLES = {signer: ["Needs to sign", "✎", "Signs and fills in their fields"], in_person: ["In person signer", "⌂", "Signs on your device; you host the signing"],
    cc: ["Receives a copy", "⧉", "Gets the completed documents; no action"], viewer: ["Needs to view", "◉", "Reviews the documents; does not sign"]};
  const SIGNING = ["signer", "in_person"];
  const KINDS = {signature: ["Signature", 170, 40, "sig"], initials: ["Initials", 52, 24, "ini"], date_signed: ["Date signed", 110, 16, "cal"], name: ["Full name", 150, 16, "user"],
    first_name: ["First name", 110, 16, "user"], last_name: ["Last name", 110, 16, "user"], email: ["Email", 170, 16, "mail"], company: ["Company", 150, 16, "org"], title: ["Title", 150, 16, "badge"],
    text: ["Text", 150, 16, "text"], number: ["Number", 90, 16, "num"], date: ["Date", 100, 16, "cal"], checkbox: ["Checkbox", 14, 14, "check"], radio: ["Radio button", 13, 13, "radio"],
    dropdown: ["Dropdown", 150, 16, "list"], attachment: ["Attachment", 130, 26, "clip"], note: ["Note", 170, 34, "note"]};
  const ICON_TEXT = {sig: "✎", ini: "AB", cal: "▦", user: "☺", mail: "@", org: "▥", badge: "◆", text: "T", num: "#", check: "☑", radio: "◉", list: "≡", clip: "⌁", note: "✉"};
  const AUTO = ["date_signed", "name", "first_name", "last_name", "email"];
  const TEXT_KINDS = ["company", "title", "text", "number", "date"];
  const VALIDATION = [["", "Any text"], ["letters", "Letters only"], ["number", "Number"], ["email", "Email address"], ["phone", "Phone number"], ["zip", "ZIP code"], ["date", "Date (MM/DD/YYYY)"]];
  const LOCKED = ["signed", "completed", "declined", "reassigned"];
  const FOLDERS = [["action", "Action required"], ["waiting", "Waiting for others"], ["expiring", "Expiring soon"], ["completed", "Completed"], ["drafts", "Drafts"],
    ["sent", "All sent"], ["declined", "Declined"], ["voided", "Voided"], ["expired", "Expired"]];
  const EMAIL_KINDS = {invite: "Signing request", reminder: "Reminder", expiring: "Expiration warning", corrected: "Corrected request", copy: "Copy", completed: "Completed copy", voided: "Voided notice", declined: "Declined notice",
    host: "In-person signing notice to the host", removed: "Removed-from-envelope notice"};
  const PERIODS = [["", "Any time"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last 12 months"]];
  let user = null, entities = [];
  const list = {box: "action", q: "", page: 0, entity: "", period: ""};
  let editor = null;
  const images = new Map();  // page images reused across editor renders, so nothing is downloaded twice

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function button(text, fn, cls = "button button-quiet") { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", event => { event.stopPropagation(); Promise.resolve().then(() => fn(event)).catch(fail); }); return b; }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : ""; }
  function day(ts) { return ts ? new Date(ts * 1000).toLocaleDateString([], {dateStyle: "medium"}) : ""; }
  function fail(e) { if (!e || !e.message) return; const box = $("app-error"); box.textContent = e.message; box.hidden = false; box.scrollIntoView({behavior: "smooth", block: "center"}); }
  let toastTimer = null;
  function ok(text) { $("app-error").hidden = true; let t = document.querySelector(".toast"); if (!t) { t = el("div", "toast"); t.setAttribute("role", "status"); document.body.append(t); } t.textContent = text; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 5000); }
  function clear() { $("app-error").hidden = true; $("status-message").hidden = true; }
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
  function chip(status, label) { return el("span", `chip chip-${TONE[status] || status}`, label || STATUS[status] || status); }
  function go(fn) { clear(); window.portalView?.("esign"); return fn(); }
  function mark(hash) { window.portalMark?.(hash); }
  function guard() { return !editor || !editor.dirty || confirm("Leave without saving your changes?"); }
  function link(href, text, cls) { const a = el("a", cls, text); a.href = href; a.setAttribute("download", ""); return a; }
  function field(label, control, note) { const wrap = el("div", "field"); const l = el("label", "", label); control.id = control.id || `esign-${Math.random().toString(36).slice(2)}`; l.htmlFor = control.id; wrap.append(l, control); if (note) wrap.append(el("p", "field-note", note)); return wrap; }
  function select(options, value) { const s = el("select"); options.forEach(([v, t]) => s.append(new Option(t, v))); s.value = value ?? options[0]?.[0] ?? ""; return s; }
  function input(value = "", type = "text", attrs = {}) { const i = el("input"); i.type = type; i.value = value ?? ""; Object.entries(attrs).forEach(([k, v]) => { i[k] = v; }); return i; }
  function menu(trigger, items, cls = "") {
    // A small dropdown: items are [label, fn, description?] or null for nothing.
    const wrap = el("div", `more-menu ${cls}`);
    trigger.setAttribute("aria-haspopup", "true"); trigger.setAttribute("aria-expanded", "false");
    const list = el("div", "menu-list"); list.setAttribute("role", "menu"); list.hidden = true;
    items.filter(Boolean).forEach(([label, fn, note]) => {
      const b = el("button"); b.type = "button"; b.setAttribute("role", "menuitem");
      b.append(el("span", "", label)); if (note) b.append(el("small", "", note));
      b.addEventListener("click", event => { event.stopPropagation(); list.hidden = true; Promise.resolve().then(fn).catch(fail); });
      list.append(b);
    });
    trigger.addEventListener("click", event => { event.stopPropagation(); const open = list.hidden; document.querySelectorAll(".menu-list").forEach(m => { m.hidden = true; }); list.hidden = !open; trigger.setAttribute("aria-expanded", String(open)); });
    wrap.append(trigger, list);
    return wrap;
  }
  function dialog(title, build, confirmText, onConfirm, danger = false) {
    const box = el("dialog", "portal-dialog");
    const form = el("form"); form.method = "dialog";
    const head = el("div", "dialog-heading"); head.append(el("h2", "", title));
    const close = el("button", "icon-button", "×"); close.type = "button"; close.setAttribute("aria-label", "Close"); close.addEventListener("click", () => box.close());
    head.append(close);
    const body = el("div", "dialog-body");
    const message = el("p", "notice notice-danger"); message.hidden = true;
    build(body);
    body.append(message);
    const foot = el("div", "dialog-footer");
    const cancel = el("button", "button button-quiet", "Cancel"); cancel.type = "button"; cancel.addEventListener("click", () => box.close());
    const confirmButton = el("button", danger ? "button button-danger" : "button button-primary", confirmText); confirmButton.type = "submit";
    foot.append(cancel, confirmButton);
    form.append(body, foot);
    form.addEventListener("submit", async event => {
      event.preventDefault(); confirmButton.disabled = true; message.hidden = true;
      try { await onConfirm(form); box.close(); }
      catch (e) { message.textContent = e.message; message.hidden = false; }
      finally { confirmButton.disabled = false; }
    });
    box.append(head, form); document.body.append(box);
    box.addEventListener("close", () => box.remove());
    box.showModal();
    return box;
  }
  function pageHead(eyebrow, title, sub, actions) {
    const head = el("div", "page-head"); const left = el("div");
    left.append(el("p", "eyebrow", eyebrow), el("h1", "", title)); if (sub) left.append(el("p", "page-sub", sub));
    head.append(left); if (actions) head.append(actions); return head;
  }
  function backLink(text, fn) { const b = el("button", "back-link", `← ${text}`); b.type = "button"; b.addEventListener("click", () => Promise.resolve().then(fn).catch(fail)); return b; }
  function trashIcon() {
    const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"), path = document.createElementNS(ns, "path");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", "18"); svg.setAttribute("height", "18"); svg.setAttribute("aria-hidden", "true");
    path.setAttribute("d", "M9 3h6l1 2h4v2H4V5h4l1-2zm-3 6h12l-1 12H7L6 9zm4 2v8h2v-8h-2zm4 0v8h2v-8h-2z"); path.setAttribute("fill", "currentColor");
    svg.append(path); return svg;
  }
  function badge(count) { const b = $("esign-count"); if (!b) return; b.textContent = String(count || ""); b.hidden = !count; }

  // ------------------------------------------------------------------ Agreements: folders and list
  async function showList(box) {
    if (!guard()) return;
    editor = null; document.body.classList.remove("tagging");
    if (box && box !== list.box) { list.box = box; list.page = 0; }
    const templates = list.box === "templates";
    mark(templates ? "#/templates" : `#/agreements/${list.box}`);
    const since = list.period ? Math.floor(Date.now() / 1000) - Number(list.period) * 86400 : 0;
    const d = await api(`/api/esign/envelopes?box=${list.box}&q=${encodeURIComponent(list.q)}&page=${list.page}&entity=${encodeURIComponent(list.entity)}&since=${since}`);
    const view = root(); view.replaceChildren();
    const actions = el("div", "page-actions");
    if (!templates) actions.append(button("Bulk send", () => showBulk()), window.homeStartMenu ? window.homeStartMenu("Start", "button button-primary") : button("New envelope", startEnvelope, "button button-primary"));
    view.append(pageHead(templates ? "Templates" : "Agreements", templates ? "Templates" : (FOLDERS.find(f => f[0] === list.box) || ["", "Agreements"])[1],
      templates ? "Reusable envelopes with their documents, recipients and fields. Use one to start a new envelope in seconds, or for bulk send."
        : "Every envelope you can see, by status. Select an envelope to see who has viewed and signed it.", actions));
    const layout = el("div", templates ? "manage manage-single" : "manage");     // Templates have no folder list beside them
    if (!templates) {
      const folders = el("nav", "folders"); folders.setAttribute("aria-label", "Envelope folders");
      folders.append(button("Send an envelope", startEnvelope, "button button-primary"));
      folders.append(el("h3", "", "Envelopes"));
      FOLDERS.forEach(([key, label]) => {
        const a = el("a", "folder"); a.href = `#/agreements/${key}`;
        a.append(el("span", "", label));
        const count = d.counts[key]; if (count) a.append(el("span", "badge-count", count));
        if (key === list.box) a.setAttribute("aria-current", "page");
        folders.append(a);
        if (key === "drafts") folders.append(el("h3", "", "History"));
      });
      folders.append(el("h3", "", "More"));
      const tpl = el("a", "folder", "Templates"); tpl.href = "#/templates"; folders.append(tpl);
      const rep = el("a", "folder", "Reports"); rep.href = "#/reports"; folders.append(rep);
      layout.append(folders);
    }
    const main = el("div");
    const search = el("form", "list-toolbar");
    const q = input(list.q, "search", {placeholder: templates ? "Search templates" : "Search subject, envelope ID, recipient name or email"}); q.setAttribute("aria-label", "Search");
    const company = select([["", "All companies"], ...entities.map(x => [x.name, x.short || x.name])], list.entity); company.setAttribute("aria-label", "Company");
    const period = select(PERIODS, list.period); period.setAttribute("aria-label", "Created");
    search.append(q); if (entities.length > 1) search.append(company); search.append(period, el("button", "button button-quiet", "Search"));
    const apply = () => { list.q = q.value.trim(); list.entity = company.value; list.period = period.value; list.page = 0; showList().catch(fail); };
    search.addEventListener("submit", event => { event.preventDefault(); apply(); });
    company.addEventListener("change", apply); period.addEventListener("change", apply);
    main.append(search);
    if (!d.envelopes.length) {
      const empty = el("div", "empty-state list-empty");
      empty.append(el("strong", "", list.box === "action" ? "You're all caught up" : templates ? "No templates yet" : "Nothing here yet"),
        el("p", "", list.box === "action" ? "Nothing is waiting for your signature." : templates ? "Open a sent envelope and choose More › Save as template." : "Envelopes appear here as soon as they match this folder."));
      if (!templates) empty.append(button("Send an envelope", startEnvelope, "button button-primary"));
      main.append(empty);
    } else {
      const card = el("div", "panel table-card"); const body = el("div", "panel-body");
      const table = el("table", "portal-table env-table");
      const head = el("tr");
      (templates ? ["Template", "Company", "Recipients", "Last change", ""] : ["Subject", "Status", "Last change", ""]).forEach(t => head.append(el("th", "", t)));
      const thead = el("thead"); thead.append(head); table.append(thead);
      const tbody = el("tbody");
      d.envelopes.forEach(e => tbody.append(templates ? templateRow(e) : row(e)));
      table.append(tbody); body.append(table); card.append(body); main.append(card);
      const paging = el("div", "portal-paging");
      const back = button("Previous", () => { list.page--; return showList(); }), more = button("Next", () => { list.page++; return showList(); });
      back.disabled = list.page === 0; more.disabled = !d.has_more;
      paging.append(back, el("span", "", `Page ${list.page + 1}`), more);
      main.append(paging);
    }
    layout.append(main);
    view.append(layout);
    badge(d.counts.action);
  }

  function open(e) { return (e.status === "draft" || e.status === "template" ? openEditor(e.id) : showEnvelope(e.id)); }
  function row(e) {
    const tr = el("tr"); tr.tabIndex = 0;
    tr.addEventListener("click", () => open(e).catch(fail));
    tr.addEventListener("keydown", event => { if (event.key === "Enter") open(e).catch(fail); });
    const subject = el("td"); const s = el("div", "env-subject"); s.append(el("span", "doc-icon", "PDF"));
    const text = el("div"); text.append(el("strong", "", e.subject || "(no subject)"));
    const people = e.recipients.filter(p => p.role !== "cc").map(p => p.name || p.email).join(", ");
    text.append(el("small", "", [people ? `To: ${people}` : "No recipients yet", e.client].filter(Boolean).join(" · ")));
    s.append(text); subject.append(s);
    const status = el("td"); const cell = el("div", "status-cell"); cell.append(chip(e.status));
    if (e.status === "sent" || e.status === "completing") {
      const [done, total] = e.progress; const bar = el("span", "mini-progress"); const fill = el("i"); fill.style.width = `${total ? done / total * 100 : 0}%`; bar.append(fill);
      cell.append(bar, el("small", "", `${done} of ${total} signed`));
    }
    if (e.attention) cell.append(el("span", "attention-flag", "Needs attention"));
    status.append(cell);
    const change = el("td", "when-cell"); change.append(el("span", "", day(e.completed_at || e.sent_at || e.updated)));
    change.append(el("small", "", e.completed_at ? "Completed" : e.sent_at ? `Sent by ${e.sender}` : "Last edited"));
    const actions = el("td", "row-actions");
    if (list.box === "action") actions.append(e.hosting ? button("Host signing", () => showEnvelope(e.id), "button button-primary button-sm") : button("Sign", () => signNow(e.id), "button button-primary button-sm"));
    else if (e.status === "completed") actions.append(link(`/api/esign/envelopes/${e.id}/combined`, "Download", "button button-quiet button-sm"));
    else if (e.status === "sent") actions.append(button("Resend", async () => { await api(`/api/esign/envelopes/${e.id}/resend`, {}); ok("Sent again to everyone who has not finished."); }, "button button-quiet button-sm"));
    else if (e.status === "draft") actions.append(button("Continue", () => openEditor(e.id), "button button-quiet button-sm"));
    actions.querySelectorAll("a").forEach(a => a.addEventListener("click", event => event.stopPropagation()));
    tr.append(subject, status, change, actions);
    return tr;
  }
  function templateRow(e) {
    const tr = el("tr"); tr.tabIndex = 0;
    tr.addEventListener("click", () => openEditor(e.id).catch(fail));
    const name = el("td"); const s = el("div", "env-subject"); s.append(el("span", "doc-icon", "TPL")); const t = el("div"); t.append(el("strong", "", e.template_name || "(unnamed)"), el("small", "", e.subject || "")); s.append(t); name.append(s);
    const people = e.recipients.map(p => p.role_name || p.name).filter(Boolean).join(", ");
    tr.append(name, el("td", "", (entities.find(x => x.name === e.entity) || {}).short || e.entity), el("td", "", people || "—"), el("td", "when-cell", day(e.updated)));
    const actions = el("td", "row-actions"); actions.append(button("Use", () => useTemplate(e), "button button-primary button-sm")); tr.append(actions);
    return tr;
  }

  async function signNow(id) { const d = await api(`/api/esign/envelopes/${id}/sign-now`, {}); location.href = d.url; }

  // ------------------------------------------------------------------ starting an envelope
  async function searchClients(term) { const d = await api(`/api/clients?q=${encodeURIComponent(term)}`); return d.clients; }
  function clientPicker(onPick, initial = null) {
    // Search the client list as you type; pick one or none.
    const wrap = el("div", "suggest-wrap");
    const box = el("div", "suggest-box"); box.hidden = true;
    const q = input(initial?.name || "", "search", {placeholder: "Search your clients (optional)"}); q.setAttribute("aria-label", "Client");
    let timer = null, seq = 0;
    const run = async () => {
      const mine = ++seq; const rows = await searchClients(q.value.trim());
      if (mine !== seq) return;
      box.replaceChildren(...rows.slice(0, 8).map(c => {
        const b = el("button"); b.type = "button"; b.append(el("strong", "", c.client_name), el("small", "", c.address || ""));
        b.addEventListener("mousedown", event => { event.preventDefault(); q.value = c.client_name; box.hidden = true; onPick({id: c.id, name: c.client_name}); });
        return b;
      }));
      box.hidden = !rows.length;
    };
    q.addEventListener("focus", () => run().catch(() => {}));
    q.addEventListener("input", () => { clearTimeout(timer); if (!q.value.trim()) onPick(null); timer = setTimeout(() => run().catch(() => {}), 200); });
    q.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 150));
    wrap.append(q, box);
    return wrap;
  }
  async function startEnvelope(clientId = null, entity = null) {
    if (!guard()) return;
    const create = async (company, client) => {
      const env = await api("/api/esign/envelopes", {entity: company, client_id: client || null});
      await go(() => openEditor(env.id, env));
    };
    if (entity || entities.length === 1) return create(entity || entities[0].name, clientId);
    let chosen = entities[0].name, client = clientId;
    dialog("Send an envelope", body => {
      body.append(el("p", "portal-note", "Choose the company the documents come from. Its logo, colours and mailbox are used for every email the recipients receive."));
      const grid = el("div", "company-pick");
      entities.forEach(e => {
        const label = el("label", "company-option"); const radio = input(e.name, "radio", {name: "company", checked: e.name === chosen});
        radio.addEventListener("change", () => { chosen = e.name; });
        const img = el("img"); img.src = `/assets/brand/${e.logo || "scribe.png"}`; img.alt = "";
        const text = el("span"); text.append(el("strong", "", e.short || e.name), el("small", "", e.name));
        label.append(radio, img, text); grid.append(label);
      });
      body.append(grid);
      body.append(field("Client (optional)", clientPicker(c => { client = c?.id || null; })), el("p", "field-note", "With a client, their generated agreements and files are offered as documents to send."));
    }, "Continue", () => create(chosen, client));
  }
  window.esignStart = () => startEnvelope().catch(fail);

  async function useTemplate(t) {
    let client = null;
    dialog(`Use template: ${t.template_name}`, body => body.append(field("Client (optional)", clientPicker(c => { client = c?.id || null; }))), "Create envelope", async () => {
      const env = await api(`/api/esign/envelopes/${t.id}/copy`, {client_id: client});
      await openEditor(env.id, env);
    });
  }

  window.esignFromContract = async contractId => {
    if (!guard()) return;
    try {
      const env = await api("/api/esign/envelopes", {contract_id: contractId});
      await go(() => openEditor(env.id, env));
      ok("Ready to send: the client's signature, name, date and initials on every page are already placed. Check the recipient, then select Next.");
    } catch (e) { fail(e); }
  };
  window.esignOpen = id => go(() => showEnvelope(id)).catch(fail);
  window.esignNewForClient = async (clientId, entity) => {
    if (!guard()) return;
    try { await startEnvelope(clientId, entity); } catch (e) { fail(e); }
  };
  window.esignOpenDraft = env => go(() => openEditor(env.id, env)).catch(fail);
  window.esignShowList = box => go(() => showList(box)).catch(fail);
  window.esignShowMailboxes = () => { clear(); showMailboxes().catch(fail); };

  // ------------------------------------------------------------------ envelope details
  async function showEnvelope(id) {
    if (!guard()) return;
    editor = null; document.body.classList.remove("tagging");
    const e = await api(`/api/esign/envelopes/${id}`);
    if (e.status === "draft" || e.status === "template") return openEditor(id, e);
    mark(`#/envelope/${id}`);
    const view = root(); view.replaceChildren();
    view.append(backLink("Agreements", () => showList()));
    const head = el("section", "env-head");
    const left = el("div");
    left.append(el("p", "eyebrow", `Envelope ${e.ref}`), el("h1", "", e.subject));
    const meta = el("div", "env-meta");
    const fact = (label, value) => { if (!value) return; const s = el("span"); s.append(el("b", "", `${label} `), document.createTextNode(value)); meta.append(s); };
    fact("From", `${e.sender.name}`); fact("Company", e.entity_short); fact("Client", e.client); fact("Sent", when(e.sent_at)); fact("Completed", when(e.completed_at));
    if (e.status === "sent") fact("Expires", e.expires_at ? day(e.expires_at) : "Never");
    left.append(meta);
    const signers = e.recipients.filter(r => SIGNING.includes(r.role) && r.status !== "reassigned");
    const counted = signers.length ? signers : e.recipients.filter(r => r.role !== "cc" && r.status !== "reassigned");
    const done = counted.filter(r => ["signed", "completed"].includes(r.status)).length;
    const progress = el("div", "env-progress"); const bar = el("span", "bar"); const fill = el("i"); fill.style.width = `${counted.length ? done / counted.length * 100 : 0}%`; bar.append(fill);
    progress.append(bar, el("span", "", `${done} of ${counted.length} ${signers.length ? "signed" : "done"}`));
    left.append(progress);
    const right = el("div", "env-actions");
    right.append(chip(e.status));
    if (e.can_sign_now) right.append(button("Sign now", () => signNow(e.id), "button button-primary"));
    (e.can_host || []).forEach(rid => {
      const r = e.recipients.find(x => x.id === rid);
      right.append(button(`Host signing: ${r.name}`, async () => {
        const d = await api(`/api/esign/envelopes/${e.id}/host/${rid}`, {});
        if (confirm(`Hand this device to ${r.name} after the signing page opens. When they finish, the page tells them to hand it back to you. Continue?`)) location.href = d.url;
      }, "button button-primary"));
    });
    if (e.status === "completed") {
      right.append(link(`/api/esign/envelopes/${e.id}/combined`, "Download", "button button-primary"), link(`/api/esign/envelopes/${e.id}/certificate`, "Certificate", "button button-quiet"));
    }
    if (e.status === "sent") {
      right.append(button("Resend", async () => { await api(`/api/esign/envelopes/${e.id}/resend`, {}); ok("The signing request was sent again to everyone who has not finished."); await showEnvelope(e.id); }));
      right.append(button("Correct", () => openEditor(e.id, e, true), "button button-quiet"));
    }
    const more = el("button", "button button-quiet"); more.type = "button"; more.append(document.createTextNode("More"), el("span", "chevron"));
    right.append(menu(more, [
      e.status === "sent" && ["Correct names, emails and access codes", () => correct(e)],
      ["Copy into a new envelope", async () => { const copy = await api(`/api/esign/envelopes/${e.id}/copy`, {}); ok("Copied. Check the recipients, then send."); await openEditor(copy.id, copy); }],
      ["Save as template", () => saveTemplate(e)],
      e.status === "completed" && (e.settings.links_off ? ["Turn recipient links back on", () => links(e, true), "The emailed links open the completed documents again"]
        : ["Turn off recipient links", () => links(e, false), "Links in emails stop opening the documents"]),
      e.status === "sent" && ["Void envelope", () => voidEnvelope(e), "Cancel it; recipients are told"],
    ]));
    head.append(left, right);
    view.append(head);
    if (e.close_reason && ["declined", "voided"].includes(e.status)) view.append(notice("danger", `${e.status === "declined" ? "Declined" : "Voided"}: ${e.close_reason}`));
    if (e.status === "completing") view.append(notice("info", e.completion_error ? `Finishing is being retried: ${e.completion_error}` : "Everyone has signed. The completed documents and certificate are being prepared and emailed."));
    if (e.status === "completed") view.append(notice("success", "Completed. Everyone received the completed documents and the Certificate of Completion by email."));
    if (e.settings.links_off) view.append(notice("warning", "Recipient links are turned off: the links in their emails no longer open these documents."));
    if (!e.mailbox.ready && e.status === "sent") view.append(notice("warning", `The ${e.entity_short} mailbox is not connected, so emails cannot be sent. A Super Admin can connect it under Admin › Company mailboxes.`));

    const grid = el("div", "env-body");
    const main = el("div"); main.style.display = "grid"; main.style.gap = "20px";
    const people = el("section", "card"); const peopleHead = el("div", "card-head"); peopleHead.append(el("h2", "", "Recipients"), el("span", "muted small", e.settings.ordered ? "Signing order set" : "Everyone at once")); people.append(peopleHead);
    const tl = el("ol", "timeline");
    const activeOrder = Math.min(...e.recipients.filter(r => ["sent", "delivered"].includes(r.status)).map(r => r.routing_order), 99);
    e.recipients.forEach(r => {
      const li = el("li");
      const finished = ["signed", "completed"].includes(r.status), problem = ["declined", "auth_failed"].includes(r.status) || ["bounced", "failed"].includes(r.delivery);
      li.append(el("span", `step-dot ${finished ? "done" : problem ? "problem" : r.routing_order === activeOrder && e.status === "sent" ? "active" : ""}`, finished ? "✓" : problem ? "!" : r.routing_order));
      const who = el("div");
      who.append(el("div", "recipient-name", r.name || r.email));
      who.append(el("div", "recipient-sub", [r.email || (r.role === "in_person" ? "Signs in person" : ""), ROLES[r.role][0]].filter(Boolean).join(" · ")));
      const events = el("div", "recipient-events");
      const ev = (label, ts) => { if (!ts) return; const s = el("span"); s.append(el("b", "", label), document.createTextNode(` ${when(ts)}`)); events.append(s); };
      ev("Sent", r.sent_at); ev("Viewed", r.delivered_at); ev(SIGNING.includes(r.role) ? "Signed" : "Done", r.signed_at); ev("Declined", r.declined_at);
      if (r.decline_reason) events.append(el("span", "", `Reason: ${r.decline_reason}`));
      if (r.auth_method && r.signed_at) events.append(el("span", "", r.auth_method));
      if (r.has_access_code) events.append(el("span", "", "Access code required"));
      if (r.role === "in_person") { const host = e.hosts.find(h => h.id === r.host_id); events.append(el("span", "", `Hosted by ${host ? host.name : "the sender"}`)); }
      if (r.reassigned_to) { const to = e.recipients.find(x => x.id === r.reassigned_to); if (to) events.append(el("span", "", `Assigned to ${to.name} (${to.email})`)); }
      if (r.saved_progress && !LOCKED.includes(r.status)) events.append(el("span", "", "Started; saved to finish later"));
      if (r.delivery === "bounced" || r.delivery === "failed") events.append(el("span", "attention-flag", `${r.delivery === "bounced" ? "Email undeliverable" : "Email not sent"}${r.delivery_error ? `: ${r.delivery_error}` : ""}`));
      if (r.reminders_sent) events.append(el("span", "", `${r.reminders_sent} reminder${r.reminders_sent === 1 ? "" : "s"}`));
      who.append(events);
      const state = el("div"); state.append(chip(finished ? "completed" : problem ? "declined" : r.status === "reassigned" ? "voided" : r.status === "created" ? "draft" : "sent", R_STATUS[r.status]));
      li.append(who, state);
      tl.append(li);
    });
    people.append(tl);
    main.append(people);

    const history = el("section", "card");
    const tabs = el("div", "tabs"); tabs.setAttribute("role", "tablist");
    const panes = el("div");
    const tab = (label, build) => {
      const b = el("button", "", label); b.type = "button"; b.setAttribute("role", "tab");
      b.addEventListener("click", () => { tabs.querySelectorAll("button").forEach(x => x.setAttribute("aria-selected", String(x === b))); panes.replaceChildren(build()); });
      tabs.append(b); return b;
    };
    const first = tab("History", () => {
      const wrap = el("div");
      wrap.append(el("p", `integrity${e.chain_ok ? "" : " bad"}`, e.chain_ok ? "✓ Audit trail verified: every event is intact and in order." : "Warning: the audit trail does not verify. An event was changed or removed outside the application."));
      const listEl = el("ol", "audit-list");
      [...e.history].reverse().forEach(h => {
        const li = el("li"); li.append(el("time", "", when(h.at)));
        const what = el("div"); what.append(el("strong", "", h.label + (h.recipient && !h.label.includes(h.recipient) ? ` · ${h.recipient}` : "")));
        const details = [h.actor, h.ip && `IP ${h.ip}`, h.detail?.to && `to ${h.detail.to}`, h.detail?.reason && `reason: ${h.detail.reason}`, h.detail?.document,
          h.detail?.added?.length && `added ${h.detail.added.join(", ")}`, h.detail?.removed?.length && `removed ${h.detail.removed.join(", ")}`,
          h.kind === "removed" && h.detail?.name && `${h.detail.name}${h.detail.email ? ` (${h.detail.email})` : ""}`, h.detail?.name && h.kind === "attachment" && h.detail.name,
          h.detail?.from && `from ${h.detail.from}`, h.detail?.attachments?.length && `attachments: ${h.detail.attachments.join(", ")}`].filter(Boolean);
        what.append(el("small", "", details.join(" · ")));
        li.append(what); listEl.append(li);
      });
      wrap.append(listEl); return wrap;
    });
    if (e.mails.length) tab(`Emails (${e.mails.length})`, () => {
      const t = el("table", "portal-table"); const h = el("tr"); ["Email", "To", "Status", "Time"].forEach(x => h.append(el("th", "", x))); const th = el("thead"); th.append(h); t.append(th);
      const tb = el("tbody");
      e.mails.forEach(m => {
        const tr = el("tr");
        const label = {queued: m.attempts ? `Retrying (attempt ${m.attempts + 1})` : "Queued", sending: "Sending", sent: "Sent", failed: "Failed", bounced: "Undeliverable"}[m.state] || m.state;
        const status = el("td"); status.append(chip(m.state === "sent" ? "completed" : m.state === "failed" || m.state === "bounced" ? "declined" : "sent", label));
        if (m.last_error && m.state !== "sent") status.append(el("small", "", m.last_error));
        const to = el("td", "", m.to_addrs.concat(m.cc_addrs).join(", ")); if (m.bcc_addrs.length) to.append(el("small", "", `Copy to ${m.bcc_addrs.join(", ")}`));
        tr.append(el("td", "", EMAIL_KINDS[m.kind] || m.kind.replace("notify_", "Notification: ")), to, status, el("td", "when-cell", when(m.sent_at || m.created)));
        tb.append(tr);
      });
      t.append(tb); const wrap = el("div"); wrap.style.overflow = "auto"; wrap.append(t); return wrap;
    });
    if (e.replies.length) tab(`Replies (${e.replies.length})`, () => {
      const wrap = el("div");
      e.replies.forEach(m => {
        const card = el("article", "reply-card");
        card.append(el("strong", "", m.kind === "bounce" ? `Undeliverable notice · ${when(m.received)}` : `${m.from_addr} · ${when(m.received)}`), el("small", "muted", ` ${m.subject}`), el("p", "", m.snippet || "(no text)"));
        if (m.files.length) { const files = el("div", "file-links"); m.files.forEach(f => files.append(link(`/api/esign/attachments/${f.id}`, `${f.name} (${Math.ceil(f.size / 1024)} KB${f.scan === "clean" ? ", virus-scanned" : ", not scanned"})`, ""))); card.append(files); }
        wrap.append(card);
      });
      return wrap;
    });
    history.append(tabs, panes); main.append(history);
    first.click();

    const side = el("div"); side.style.display = "grid"; side.style.gap = "20px";
    const docs = el("section", "card");
    const docsHead = el("div", "card-head"); docsHead.append(el("h2", "", "Documents")); docs.append(docsHead);
    const cards = el("div", "doc-cards");
    e.documents.forEach(d => {
      const card = el("div", "doc-card");
      const owner = d.source === "attachment" ? e.recipients.find(x => x.id === d.recipient_id) : null;
      card.append(el("span", "doc-icon", "PDF"));
      const text = el("div"); text.append(el("strong", "", d.name), el("small", "", `${d.pages} page${d.pages === 1 ? "" : "s"}${owner ? ` · attached by ${owner.name} while signing` : ""}`));
      card.append(text);
      const links = el("div", "file-links");
      links.append(link(`/api/esign/documents/${d.id}/file`, "Original", ""));
      if (d.final_sha256) links.append(link(`/api/esign/documents/${d.id}/file?final=1`, "Completed", ""));
      card.append(links); cards.append(card);
    });
    docs.append(cards);
    if (e.status === "completed") { const all = el("div", "file-links"); all.style.marginTop = "12px"; all.append(link(`/api/esign/envelopes/${e.id}/combined`, "All completed documents + certificate", ""), link(`/api/esign/envelopes/${e.id}/certificate`, "Certificate of Completion", "")); docs.append(all); }
    const details = el("section", "card"); const detailsHead = el("div", "card-head"); detailsHead.append(el("h2", "", "Details")); details.append(detailsHead);
    const facts = el("ul", "summary-list");
    [["Status", STATUS[e.status]], ["Envelope ID", e.ref], ["Created", when(e.created)], ["Sent", when(e.sent_at)], ["Completed", when(e.completed_at)],
     ["Expires", e.status === "sent" ? (e.expires_at ? when(e.expires_at) : "Never") : ""], ["Reminders", e.settings.reminder_every ? `After ${e.settings.reminder_delay} days, then every ${e.settings.reminder_every}` : "Off"]]
      .filter(([, v]) => v).forEach(([k, v]) => { const li = el("li"); li.append(el("span", "", k), el("strong", "", v)); facts.append(li); });
    details.append(facts);
    if (e.message) { details.append(el("h3", "", "Message")); const m = el("p", "muted small", e.message); m.style.whiteSpace = "pre-wrap"; details.append(m); }
    side.append(docs, details);
    grid.append(main, side);
    view.append(grid);
  }
  function notice(tone, text) { return el("div", `notice notice-${tone}`, text); }
  async function links(e, active) {
    if (!active && !confirm("Turn off the links in the recipients' emails? They will no longer open these documents. You can turn them back on later.")) return;
    await api(`/api/esign/envelopes/${e.id}/links`, {active}); ok(active ? "Recipient links are on again." : "Recipient links are turned off."); await showEnvelope(e.id);
  }

  function correct(e) {
    const openPeople = e.recipients.filter(r => !LOCKED.includes(r.status) && r.role !== "in_person");
    if (!openPeople.length) { fail(new Error("Everyone has finished; there is nothing to correct.")); return; }
    dialog("Correct recipients", body => {
      body.append(el("p", "portal-note", "Change the details of recipients who have not finished. Anyone whose email or access code changes gets a new email; their earlier link stops working."));
      openPeople.forEach(r => {
        const box = el("fieldset", "card"); box.dataset.id = r.id; box.style.margin = "0 0 12px";
        box.append(el("legend", "", `${r.name} · ${ROLES[r.role][0]}`));
        const two = el("div", "settings-grid");
        two.append(field("Name", input(r.name, "text", {maxLength: 150, name: "name"})), field("Email", input(r.email, "email", {maxLength: 200, name: "email"})));
        box.append(two);
        box.append(field(r.has_access_code ? "New access code (leave blank to keep it)" : "Access code (optional)", input("", "text", {maxLength: 50, name: "access_code", autocomplete: "off"})));
        if (r.has_access_code) { const l = el("label", "check-inline"); const c = input("", "checkbox", {name: "remove_code"}); l.append(c, document.createTextNode("Remove the access code")); box.append(l); }
        if (r.status === "auth_failed") { const l = el("label", "check-inline"); const c = input("", "checkbox", {name: "unlock", checked: true}); l.append(c, document.createTextNode("Unlock access")); box.append(l); }
        box.append(field("Private message", Object.assign(el("textarea"), {value: r.note, maxLength: 1000, name: "note", rows: 2})));
        body.append(box);
      });
      body.append(field("Expiration", select([["", "Keep as it is"], ["0", "Never expires"], ["7", "7 days from today"], ["14", "14 days from today"], ["30", "30 days from today"], ["60", "60 days from today"], ["90", "90 days from today"]], ""), "Links never expire unless you set an expiration."));
      body.lastChild.querySelector("select").name = "expire_days";
    }, "Save corrections", async form => {
      const recipients = [...form.querySelectorAll("fieldset")].map(box => {
        const get = name => box.querySelector(`[name="${name}"]`);
        const item = {id: box.dataset.id, name: get("name").value, email: get("email").value, note: get("note").value};
        if (get("access_code").value) item.access_code = get("access_code").value;
        else if (get("remove_code")?.checked) item.access_code = "";
        if (get("unlock")?.checked) item.unlock = true;
        return item;
      });
      const days = form.querySelector('[name="expire_days"]').value;
      await api(`/api/esign/envelopes/${e.id}/correct`, {recipients, expire_days: days === "" ? null : Number(days)});
      ok("The envelope was corrected.");
      await showEnvelope(e.id);
    });
  }

  function voidEnvelope(e) {
    dialog("Void envelope", body => {
      body.append(el("p", "portal-note", "Voiding cancels the envelope. Recipients who already received it are told, with your reason. This cannot be undone."));
      body.append(field("Reason", Object.assign(el("textarea"), {maxLength: 500, rows: 3, required: true})));
    }, "Void envelope", async form => {
      await api(`/api/esign/envelopes/${e.id}/void`, {reason: form.querySelector("textarea").value});
      ok("The envelope was voided.");
      await showEnvelope(e.id);
    }, true);
  }

  function saveTemplate(e) {
    dialog("Save as template", body => body.append(field("Template name", input(e.subject.slice(0, 150), "text", {maxLength: 150, required: true}))), "Save template", async form => {
      await api(`/api/esign/envelopes/${e.id}/copy`, {template_name: form.querySelector("input").value});
      ok("Template saved. Find it under Templates.");
    });
  }

  // ------------------------------------------------------------------ the send wizard (documents, recipients, message, settings) and Add fields
  let keySeq = 0;
  async function openEditor(id, data, correcting = false, stage = "setup") {
    const e = data || await api(`/api/esign/envelopes/${id}`);
    const keyOf = new Map(e.fields.map(f => [f.id, `f${++keySeq}`]));
    editor = {e, correcting, stage, library: null, recipients: e.recipients.map(r => ({...r, key: r.id, access_code: null, locked: correcting && (LOCKED.includes(r.status)),
      show: {code: !!r.has_access_code, note: !!r.note, label: false}})),
      fields: e.fields.map(f => {
        const options = {...(f.options || {})};
        if (options.condition) options.condition = {...options.condition, field: keyOf.get(options.condition.field) || ""};
        return {...f, options, recipient: f.recipient_id, key: keyOf.get(f.id)};
      }), active: null, selected: null, placing: null, dirty: false};
    if (!("ordered" in e.settings)) e.settings.ordered = new Set(e.recipients.filter(r => r.role !== "cc").map(r => r.routing_order)).size > 1;
    editor.active = editor.recipients.find(r => SIGNING.includes(r.role) && !r.locked)?.key || editor.recipients.find(r => !r.locked)?.key || null;
    mark(`#/envelope/${e.id}`);
    renderEditor();
  }
  function color(key) { const index = editor.recipients.filter(r => SIGNING.includes(r.role)).findIndex(r => r.key === key); return COLORS[(index < 0 ? 7 : index) % COLORS.length]; }
  function dirty() { if (editor) { editor.dirty = true; refreshAside(); } }
  function lockedKey(key) { return !!editor.recipients.find(r => r.key === key)?.locked; }
  function renderEditor() { if (editor.stage === "fields") renderTagger(); else renderSetup(); }

  function renderSetup() {
    const e = editor.e, template = e.status === "template", correcting = editor.correcting;
    document.body.classList.remove("tagging");
    const view = root(); view.replaceChildren();
    view.append(backLink(correcting ? "Envelope" : template ? "Templates" : "Agreements", () => { if (!guard()) return; editor = null; return correcting ? showEnvelope(e.id) : showList(template ? "templates" : "drafts"); }));
    const companyName = (entities.find(x => x.name === e.entity) || {}).short || e.entity;
    view.append(pageHead(correcting ? `Correct envelope ${e.ref}` : template ? "Template" : `New envelope · ${e.ref}`,
      correcting ? "Correct recipients" : template ? (e.template_name || "Template") : "Prepare your envelope",
      correcting ? "Change, add or remove recipients who have not finished, then adjust their fields. People who have signed stay as they are."
        : `Sent from ${companyName}${e.client ? ` · Client: ${e.client}` : ""}. Add documents and recipients, then place the fields.`));
    if (!e.mailbox.ready && !template) view.append(notice("warning", `The ${e.entity_short} mailbox is not connected yet. You can prepare this envelope, but sending needs a Super Admin to connect it under Admin › Company mailboxes.`));
    const wizard = el("div", "wizard"); const main = el("div", "wizard-main");
    let n = 0;
    if (!correcting) main.append(documentsSection(++n));
    main.append(recipientsSection(++n));
    if (!correcting) { main.append(messageSection(++n)); main.append(settingsSection(++n)); }
    const aside = el("aside", "wizard-aside"); aside.id = "wizard-aside";
    wizard.append(main, aside);
    view.append(wizard);
    const bar = el("div", "wizard-bar");
    const left = el("div", "left"); left.id = "wizard-state";
    const right = el("div", "right");
    if (!correcting) {
      right.append(button("Delete", async () => {
        if (!confirm(template ? "Delete this template?" : "Delete this draft envelope?")) return;
        await api(`/api/esign/envelopes/${e.id}/delete`, {}); editor = null; ok(template ? "Template deleted." : "Draft deleted."); await showList(template ? "templates" : "drafts");
      }, "button button-ghost"));
      right.append(button(template ? "Save template" : "Save draft", async () => { await save(); ok("Saved."); }));
    }
    right.append(button("Next: Add fields →", toFields, "button button-primary"));
    bar.append(left, right);
    view.append(bar);
    refreshAside();
  }

  function section(n, title, sub, extra) {
    const s = el("section", "wizard-section"); const head = el("header");
    head.append(el("span", "num", n)); const t = el("div"); t.append(el("h2", "", title)); if (sub) t.append(el("p", "", sub)); head.append(t, el("span", "spacer"));
    if (extra) head.append(extra);
    const body = el("div", "wizard-body"); s.append(head, body); return [s, body];
  }

  function documentsSection(n) {
    const e = editor.e;
    const [s, body] = section(n, "Add documents", "PDF, Word, text or images. Word files and images are converted to PDF.");
    if (e.documents.length) {
      const grid = el("div", "doc-grid");
      e.documents.filter(d => d.source !== "attachment").forEach(d => {
        const card = el("div", "doc-thumb");
        const thumb = el("div", "thumb"); const img = el("img"); img.loading = "lazy"; img.alt = ""; img.src = `/api/esign/documents/${d.id}/page/1.png`; thumb.append(img);
        const meta = el("div", "meta"); meta.append(el("strong", "", d.name), el("small", "", `${d.pages} page${d.pages === 1 ? "" : "s"}`));
        card.append(thumb, meta);
        if (d.source === "generated") card.append(el("span", "source-tag", "Agreement"));
        const remove = button("×", async () => {
          if (!confirm(`Remove ${d.name} and its fields?`)) return;
          await save(true); const env = await api(`/api/esign/envelopes/${e.id}/documents/${d.id}/remove`, {}); await openEditor(env.id, env);
        }, "remove"); remove.setAttribute("aria-label", `Remove ${d.name}`);
        card.append(remove); grid.append(card);
      });
      body.append(grid);
    }
    const drop = el("div", "dropzone");
    drop.append(el("span", "upload-icon", "↑"), el("strong", "", e.documents.length ? "Add more documents" : "Drop documents here"), el("small", "", "or choose files from your computer · up to 15 MB each"));
    const file = input("", "file", {accept: ".pdf,.docx,.doc,.odt,.rtf,.txt,.png,.jpg,.jpeg", multiple: true}); file.hidden = true;
    const actions = el("div", "dropzone-actions"); actions.append(button("Upload", () => file.click(), "button button-primary"));
    drop.append(actions, file);
    const upload = async files => {
      await save(true);
      let env;
      for (const f of files) {
        if (f.size > 15000000) throw new Error(`${f.name} is larger than 15 MB.`);
        ok(`Adding ${f.name}…`);
        const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.onerror = reject; r.readAsDataURL(f); });
        env = await api(`/api/esign/envelopes/${e.id}/documents`, {name: f.name, data});
      }
      if (env) { await openEditor(env.id, env); ok(`Added ${files.length} document${files.length === 1 ? "" : "s"}.`); }
    };
    file.addEventListener("change", () => upload([...file.files]).catch(fail));
    ["dragenter", "dragover"].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); drop.classList.add("drag"); }));
    ["dragleave", "drop"].forEach(type => drop.addEventListener(type, () => drop.classList.remove("drag")));
    drop.addEventListener("drop", event => { event.preventDefault(); if (event.dataTransfer.files.length) upload([...event.dataTransfer.files]).catch(fail); });
    body.append(drop);
    // The client's documents: generated agreements and files on the profile, one click to add.
    const lib = el("div", "library");
    if (e.status !== "template") {
      const head = el("div", "card-head");
      const title = el("div"); title.append(el("h3", "", e.client ? `From ${e.client}'s profile` : "Send to a client"), el("p", "muted small", e.client ? "Agreements generated for this client and files on their profile." : "Choose a client to offer their generated agreements and files."));
      head.append(title);
      lib.append(head);
      const pick = clientPicker(async c => {
        if (!c) return;
        e.client_id = c.id; editor.library = null; await save(true); renderSetup();
      }, e.client ? {name: e.client} : null);
      if (!e.client) lib.append(pick);
      const items = el("div", "library-list"); lib.append(items);
      if (e.client) loadLibrary(items).catch(fail);
      body.append(lib);
    }
    return s;
  }
  async function loadLibrary(target) {
    const e = editor.e;
    if (!editor.library) editor.library = (await api(`/api/esign/envelopes/${e.id}/library`)).items;
    const items = editor.library;
    target.replaceChildren();
    if (!items.length) { target.append(el("p", "muted small", "No generated agreements or files for this client yet.")); return; }
    items.forEach(item => {
      const added = item.source === "generated" && e.documents.some(d => d.source_document_id === item.id);
      const row = el("div", `library-item${added ? " added" : ""}`);
      if (!item.usable) row.setAttribute("aria-disabled", "true");
      row.append(el("span", "doc-icon", item.source === "generated" ? "PDF" : (item.name.split(".").pop() || "").toUpperCase().slice(0, 4)));
      const text = el("div");
      const title = el("strong", "", item.title);
      text.append(title);
      const bits = [item.source === "generated" ? `Generated ${day(item.created)}` : `Uploaded ${day(item.created)}`, item.source === "generated" && !item.usable ? `belongs to ${item.entity}` : "",
        item.source === "file" && item.category ? item.category : ""].filter(Boolean);
      const small = el("small", "", bits.join(" · ")); text.append(small);
      if (item.latest && item.source === "generated") title.append(" ", el("span", "chip chip-info chip-plain", "Latest"));
      row.append(text);
      if (added) row.append(el("span", "chip chip-success", "Added"));
      else row.append(button("Add", async () => {
        await save(true);
        ok(`Adding ${item.title}…`);
        const env = await api(`/api/esign/envelopes/${e.id}/library`, {source: item.source, id: item.id});
        const library = editor.library;
        await openEditor(env.id, env); editor.library = library;
        ok(`${item.title} added.`);
      }, "button button-quiet button-sm"));
      if (!item.usable) row.lastChild.disabled = true;
      target.append(row);
    });
  }

  // Address book: suggestions from client contacts, earlier recipients and your team as you type a name or email.
  function suggest(target, pick) {
    const box = el("div", "suggest-box"); box.hidden = true; box.setAttribute("role", "listbox");
    let timer = null, seq = 0;
    target.setAttribute("autocomplete", "off");
    target.addEventListener("input", () => {
      clearTimeout(timer);
      const q = target.value.trim();
      if (q.length < 2) { box.hidden = true; return; }
      timer = setTimeout(async () => {
        const mine = ++seq;
        try {
          const d = await api(`/api/contacts?q=${encodeURIComponent(q)}`);
          if (mine !== seq) return;
          box.replaceChildren(...d.contacts.slice(0, 8).map(c => {
            const option = el("button"); option.type = "button"; option.setAttribute("role", "option");
            option.append(el("strong", "", c.name), el("small", "", [c.email, c.title, c.client].filter(Boolean).join(" · ")));
            option.addEventListener("mousedown", event => { event.preventDefault(); pick(c); box.hidden = true; });
            return option;
          }));
          box.hidden = !d.contacts.length;
        } catch (_) { box.hidden = true; }
      }, 200);
    });
    target.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 150));
    return box;
  }

  function roleSelect(r, onChange, disabled) {
    const wrap = el("div", "role-select");
    const trigger = el("button", "role-button"); trigger.type = "button"; trigger.disabled = !!disabled;
    trigger.append(el("span", `role-icon ${r.role}`, ROLES[r.role][1]), el("span", "", ROLES[r.role][0]), el("span", "chevron"));
    trigger.setAttribute("aria-label", `Action: ${ROLES[r.role][0]}`);
    const list = el("div", "menu-list"); list.hidden = true; list.setAttribute("role", "menu");
    Object.entries(ROLES).forEach(([key, [label, icon, note]]) => {
      const b = el("button"); b.type = "button"; b.setAttribute("role", "menuitemradio"); b.setAttribute("aria-checked", String(key === r.role));
      const text = el("span"); text.append(el("strong", "", label), el("small", "", note));
      b.append(el("span", `role-icon ${key}`, icon), text);
      b.addEventListener("click", event => { event.stopPropagation(); list.hidden = true; onChange(key); });
      list.append(b);
    });
    trigger.addEventListener("click", event => { event.stopPropagation(); const open = list.hidden; document.querySelectorAll(".menu-list").forEach(m => { m.hidden = true; }); list.hidden = !open; });
    wrap.append(trigger, list);
    return wrap;
  }

  function recipientsSection(n) {
    const e = editor.e;
    const order = el("label", "order-toggle"); const ordered = input("", "checkbox", {checked: !!e.settings.ordered});
    order.append(ordered, document.createTextNode("Set signing order"));
    ordered.addEventListener("change", () => {
      e.settings.ordered = ordered.checked;
      const open = editor.recipients.filter(r => !r.locked);
      if (ordered.checked) open.forEach((r, i) => { r.routing_order = Math.min(20, i + 1 + Math.max(0, ...editor.recipients.filter(x => x.locked).map(x => x.routing_order))); });
      else open.forEach(r => { r.routing_order = 1; });
      dirty(); renderSetup();
    });
    const [s, body] = section(n, "Add recipients", e.settings.ordered ? "Recipients act in order: number 1 first, then 2, and so on. The same number means at the same time."
      : "Everyone receives the envelope at the same time. The envelope completes when every signer has signed; viewers never hold it up.", order);
    const cards = el("div", "recipient-cards");
    editor.recipients.sort((a, b) => a.routing_order - b.routing_order).forEach(r => cards.append(recipientCard(r)));
    if (!editor.recipients.length) cards.append(el("p", "muted", "No recipients yet. Add the people who sign, view or receive a copy."));
    body.append(cards);
    const add = (values = {}) => {
      const key = `n${++keySeq}`;
      const next = e.settings.ordered ? Math.min(20, Math.max(0, ...editor.recipients.map(r => r.routing_order)) + 1) : 1;
      editor.recipients.push({key, role: "signer", role_name: `Recipient ${editor.recipients.length + 1}`, routing_order: next, name: "", email: "", note: "", access_code: null, show: {}, ...values});
      editor.active = key; dirty(); renderSetup();
      setTimeout(() => document.querySelector(`[data-recipient="${key}"] input[type=text]`)?.focus(), 0);
    };
    const row = el("div", "add-row");
    row.append(button("+ Add recipient", () => add(), "button button-quiet"), button("+ Add me", () => add({name: user.name, email: user.email, role_name: "Me"}), "button button-ghost"));
    body.append(row);
    return s;
  }

  function recipientCard(r) {
    const e = editor.e;
    const card = el("div", `recipient-card${r.locked ? " locked" : ""}`); card.dataset.recipient = r.key;
    card.style.setProperty("--who", SIGNING.includes(r.role) ? color(r.key) : "#c3cad6");
    const ord = el("div", "order");
    if (e.settings.ordered) {
      const notified = editor.correcting && r.status && r.status !== "created";
      const o = input(r.routing_order, "number", {min: 1, max: 20, disabled: notified || r.locked}); o.setAttribute("aria-label", "Signing order");
      o.addEventListener("change", () => { r.routing_order = Math.max(1, Math.min(20, Number(o.value) || 1)); dirty(); renderSetup(); });
      ord.append(o);
    } else ord.append(el("span", "drag", "⋮⋮"));
    card.append(ord);
    const grid = el("div", "grid");
    if (r.locked) {
      const who = el("div"); who.append(el("strong", "", r.name), el("div", "muted small", `${r.email} · ${R_STATUS[r.status] || "Finished"}`));
      grid.append(who); card.append(grid); return card;
    }
    const name = input(r.name, "text", {maxLength: 150, placeholder: "Full name"}); name.addEventListener("input", () => { r.name = name.value; dirty(); });
    const mail = input(r.email, "email", {maxLength: 200, placeholder: r.role === "in_person" ? "Optional, to send them a copy" : "name@example.com"});
    mail.addEventListener("input", () => { r.email = mail.value.trim(); dirty(); });
    const fill = c => { r.name = c.name; r.email = c.email; name.value = c.name; mail.value = c.email; dirty(); };
    const nameField = field("Name", name), mailField = field(r.role === "in_person" ? "Email (optional)" : "Email", mail);
    nameField.classList.add("suggest-wrap"); mailField.classList.add("suggest-wrap");
    nameField.append(suggest(name, fill)); mailField.append(suggest(mail, fill));
    const notified = editor.correcting && r.status && r.status !== "created";
    const role = roleSelect(r, value => {
      if (!SIGNING.includes(value) && editor.fields.some(f => f.recipient === r.key) && !confirm("Only signers have fields. Remove this recipient's fields?")) return;
      r.role = value; if (!SIGNING.includes(value)) editor.fields = editor.fields.filter(f => f.recipient !== r.key);
      if (value === "in_person" && !r.host_id) r.host_id = user.id;
      dirty(); renderSetup();
    }, notified);
    const roleField = el("div", "field"); roleField.append(el("label", "", "Action"), role);
    grid.append(nameField, mailField, roleField);
    card.append(grid);
    const tools = el("div", "tools");
    if (r.role_name && !/^Recipient \d+$/.test(r.role_name)) tools.append(el("span", "chip chip-neutral chip-plain", r.role_name));
    const customize = el("button", "button button-ghost button-sm"); customize.type = "button"; customize.append(document.createTextNode("Customize"), el("span", "chevron"));
    const toggle = key => () => { r.show = {...r.show, [key]: !r.show?.[key]}; renderSetup(); };
    tools.append(menu(customize, [
      r.role !== "in_person" && [r.show?.code ? "Hide access code" : "Add access code", toggle("code"), "A code you share separately; required to open the envelope"],
      [r.show?.note ? "Hide private message" : "Add private message", toggle("note"), "Shown only to this recipient"],
      [r.show?.label ? "Hide recipient label" : "Edit recipient label", toggle("label"), "A role such as Client or Company signer, used in templates"],
    ]));
    const remove = button("", () => {
      if (editor.fields.some(f => f.recipient === r.key) && !confirm("Remove this recipient and their fields?")) return;
      editor.recipients = editor.recipients.filter(x => x !== r); editor.fields = editor.fields.filter(f => f.recipient !== r.key);
      if (editor.active === r.key) editor.active = editor.recipients.find(x => SIGNING.includes(x.role))?.key || null; dirty(); renderSetup();
    }, "icon-button"); remove.append(trashIcon()); remove.setAttribute("aria-label", `Remove ${r.name || "recipient"}`); remove.title = "Remove recipient";
    tools.append(remove);
    card.append(tools);
    const extra = el("div", "extra");
    if (r.role === "in_person") {
      const hosts = editor.e.hosts || [];
      const host = select(hosts.map(h => [h.id, `${h.name} (${h.email})`]), r.host_id || user.id);
      host.addEventListener("change", () => { r.host_id = host.value; dirty(); });
      extra.append(field("Host (opens the signing on their device)", host));
    }
    if (r.show?.code && r.role !== "in_person") {
      const code = input("", "text", {maxLength: 50, placeholder: r.has_access_code ? "Saved · type to replace" : "e.g. 4821", autocomplete: "off"});
      code.addEventListener("input", () => { r.access_code = code.value; dirty(); });
      const f = field("Access code (tell them separately)", code);
      if (r.has_access_code) { const l = el("label", "check-inline"); const c = input("", "checkbox"); c.addEventListener("change", () => { r.access_code = c.checked ? "" : null; dirty(); }); l.append(c, document.createTextNode("Remove the access code")); f.append(l); }
      extra.append(f);
    }
    if (r.show?.note) {
      const note = Object.assign(el("textarea"), {value: r.note || "", maxLength: 1000, rows: 2}); note.addEventListener("input", () => { r.note = note.value; dirty(); });
      extra.append(field("Private message", note));
    }
    if (r.show?.label) {
      const label = input(r.role_name || "", "text", {maxLength: 60, placeholder: "e.g. Client"}); label.addEventListener("input", () => { r.role_name = label.value; dirty(); });
      extra.append(field("Recipient label", label));
    }
    if (extra.children.length) card.append(extra);
    return card;
  }

  function messageSection(n) {
    const e = editor.e, template = e.status === "template";
    const [s, body] = section(n, "Add message", "Recipients see the subject and message in the email and on the signing page.");
    if (template) {
      const tname = input(e.template_name, "text", {maxLength: 150}); tname.addEventListener("input", () => { e.template_name = tname.value; dirty(); });
      body.append(field("Template name", tname));
    }
    const subject = input(e.subject, "text", {maxLength: 200, placeholder: "Please sign: Services Agreement"}); subject.addEventListener("input", () => { e.subject = subject.value; dirty(); });
    const message = Object.assign(el("textarea"), {value: e.message, maxLength: 4000, rows: 5, placeholder: "Add a short note for the recipients"});
    message.addEventListener("input", () => { e.message = message.value; dirty(); });
    body.append(field("Email subject", subject), field("Message", message, template ? "For bulk send, {{name}}, {{first_name}} and {{client}} are replaced with each recipient's details." : undefined));
    return s;
  }

  function settingsSection(n) {
    const sset = editor.e.settings;
    const [s, body] = section(n, "Settings", "Reminders, expiration and what recipients may do.");
    const grid = el("div", "settings-grid");
    const reminders = select([["0", "Off"], ["1", "Every day"], ["2", "Every 2 days"], ["3", "Every 3 days"], ["7", "Every week"]], String(sset.reminder_every));
    if (![...reminders.options].some(o => o.value === String(sset.reminder_every))) reminders.append(new Option(`Every ${sset.reminder_every} days`, String(sset.reminder_every))), reminders.value = String(sset.reminder_every);
    reminders.addEventListener("change", () => { sset.reminder_every = Number(reminders.value); dirty(); });
    const first = input(sset.reminder_delay, "number", {min: 1, max: 30}); first.addEventListener("change", () => { sset.reminder_delay = Number(first.value) || 1; dirty(); });
    const expiry = select([["0", "Never expires"], ["7", "7 days"], ["14", "14 days"], ["30", "30 days"], ["60", "60 days"], ["90", "90 days"], ["180", "180 days"], ["365", "1 year"]], String(sset.expire_days || 0));
    if (![...expiry.options].some(o => o.value === String(sset.expire_days || 0))) { expiry.append(new Option(`${sset.expire_days} days`, String(sset.expire_days))); expiry.value = String(sset.expire_days); }
    const warn = input(sset.warn_days, "number", {min: 0, max: 30});
    warn.addEventListener("change", () => { sset.warn_days = Number(warn.value) || 0; dirty(); });
    const warnField = field("Warn before it expires (days)", warn);
    warnField.hidden = !Number(sset.expire_days);
    expiry.addEventListener("change", () => { sset.expire_days = Number(expiry.value); warnField.hidden = !sset.expire_days; dirty(); });
    grid.append(field("Send reminders", reminders), field("First reminder after (days)", first), field("Expiration", expiry, "Links never expire unless you choose an expiration."), warnField);
    body.append(grid);
    const flag = (key, text, note) => {
      const row = el("label", "switch-row"); const c = input("", "checkbox", {checked: sset[key] !== false}); c.addEventListener("change", () => { sset[key] = c.checked; dirty(); });
      const t = el("span"); t.append(document.createTextNode(text), el("small", "", note)); row.append(c, t); return row;
    };
    const flags = el("div"); flags.style.marginTop = "14px";
    flags.append(flag("attach_completed", "Attach the completed documents to the final email", "Everyone also gets a secure link to download them."),
      flag("allow_reassign", "Recipients may assign the documents to someone else", "For example to the person with signing authority."));
    body.append(flags);
    return s;
  }

  function refreshAside() {
    const aside = $("wizard-aside"); if (!aside || !editor) return;
    const e = editor.e;
    aside.replaceChildren();
    const card = el("section", "card"); const head = el("div", "card-head"); head.append(el("h2", "", "Summary")); card.append(head);
    const facts = el("ul", "summary-list");
    const signers = editor.recipients.filter(r => SIGNING.includes(r.role));
    [["Company", (entities.find(x => x.name === e.entity) || {}).short || e.entity], ["Client", e.client || "—"], ["Documents", e.documents.filter(d => d.source !== "attachment").length],
     ["Signers", signers.length], ["Others", editor.recipients.length - signers.length], ["Fields placed", editor.fields.length],
     ["Signing order", e.settings.ordered ? "Set" : "Everyone at once"], ["Expires", Number(e.settings.expire_days) ? `${e.settings.expire_days} days after sending` : "Never"]]
      .forEach(([k, v]) => { const li = el("li"); li.append(el("span", "", k), el("strong", "", v)); facts.append(li); });
    card.append(facts);
    const found = setupProblems();
    if (found.length) { const ul = el("ul", "problems"); found.forEach(p => ul.append(el("li", "", p))); card.append(ul); }
    aside.append(card);
    const tip = el("section", "card"); tip.append(el("h3", "", "What happens next"));
    tip.append(el("p", "muted small", "Select Next to place fields. Signature, name, date and initials on every page are placed for you, and you can move, add or delete any of them. Then select Send."));
    aside.append(tip);
    const state = $("wizard-state"); if (state) state.textContent = editor.dirty ? "Unsaved changes" : "All changes saved";
  }

  function setupProblems() {
    const out = [], e = editor.e;
    if (!editor.correcting && !e.documents.filter(d => d.source !== "attachment").length) out.push("Add at least one document.");
    if (!editor.recipients.some(r => r.role !== "cc")) out.push("Add at least one recipient who signs or views.");
    editor.recipients.filter(r => !r.locked).forEach(r => {
      if (r.role === "in_person" ? !r.name : !r.name || !r.email) out.push(r.role === "in_person" ? "Enter the name of each in-person signer." : "Enter the name and email of every recipient.");
      else if (r.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.email)) out.push(`Check the email address of ${r.name}.`);
    });
    if (!editor.correcting && e.status !== "template" && !e.subject.trim()) out.push("Enter an email subject.");
    return [...new Set(out)];
  }

  async function toFields() {
    const found = setupProblems().filter(p => !p.startsWith("Enter an email subject"));
    if (found.length) { fail(new Error(found.join(" "))); return; }
    if (!editor.correcting) await save(true);
    editor.stage = "fields";
    const placed = await autoPlace(false);
    editor.active = editor.recipients.find(r => SIGNING.includes(r.role) && !r.locked)?.key || editor.active;
    renderTagger();
    if (placed) ok(`Placed ${placed} field${placed === 1 ? "" : "s"} for you: signature, name, date and initials on every page. Drag any field to adjust it.`);
  }

  // Suggested fields from the server: signing blocks (signature, name, title, date) and initials for each page, assigned to signers in order.
  async function autoPlace(replace, onlyKey = null) {
    const e = editor.e;
    const signers = editor.recipients.filter(r => SIGNING.includes(r.role) && !r.locked).sort((a, b) => a.routing_order - b.routing_order);
    if (!signers.length || !e.documents.length) return 0;
    const d = await api(`/api/esign/envelopes/${e.id}/suggestions?signers=${signers.length}`);
    let added = 0;
    e.documents.filter(doc => doc.source !== "attachment").forEach(doc => {
      const s = d.documents[doc.id]; if (!s) return;
      const mine = f => f.document_id === doc.id && !lockedKey(f.recipient) && (!onlyKey || f.recipient === onlyKey);
      if (!replace && editor.fields.some(f => f.document_id === doc.id)) return;
      if (replace) editor.fields = editor.fields.filter(f => !mine(f));
      let order = signers;
      if (s.generated) { const client = signers.find(r => /client/i.test(r.role_name || "")) || signers[0]; order = [client, ...signers.filter(r => r !== client)]; }
      const push = (who, page, f) => {
        if (onlyKey && who.key !== onlyKey) return;
        editor.fields.push({key: `f${++keySeq}`, recipient: who.key, document_id: doc.id, page, kind: f.kind, x: f.x, y: f.y, w: f.w, h: f.h, required: true, label: "", options: {}}); added++;
      };
      s.blocks.forEach((block, i) => { const who = order[i]; if (who) block.fields.forEach(f => push(who, block.page, f)); });
      order.forEach((who, i) => {
        if (s.generated && i > 0) return;          // agreements generated here: the client initials; the company has signed already
        (s.initials[i] || []).forEach((spot, p) => push(who, p + 1, {...spot, kind: "initials"}));
      });
    });
    if (added) editor.dirty = true;
    return added;
  }

  // ------------------------------------------------------------------ Add fields
  function renderTagger() {
    const e = editor.e, template = e.status === "template", correcting = editor.correcting;
    document.body.classList.add("tagging");
    const view = root(); view.replaceChildren();
    const top = el("div", "tagger-top");
    top.append(button("← Back", () => { editor.stage = "setup"; renderSetup(); }, "button button-quiet"));
    top.append(el("h1", "", "Add fields"));
    const signers = editor.recipients.filter(r => SIGNING.includes(r.role) && !r.locked);
    if (!signers.some(r => r.key === editor.active)) editor.active = signers[0]?.key || null;
    const active = signers.find(r => r.key === editor.active);
    const who = el("div", "who-picker");
    const whoButton = el("button", "who-button"); whoButton.type = "button";
    whoButton.style.setProperty("--who", active ? color(active.key) : "#9a9a9a");
    whoButton.append(el("span", "who-swatch"), el("span", "", active ? active.name || active.email || "Signer" : "No signer"), el("span", "chevron"));
    who.append(menu(whoButton, signers.map(r => [`${r.name || r.email || "Signer"}`, () => { editor.active = r.key; editor.placing = null; renderTagger(); }, ROLES[r.role][0]])));
    top.append(who, el("span", "spacer"));
    top.append(button("Auto-place fields", async () => {
      if (!active) return;
      if (editor.fields.some(f => f.recipient === active.key) && !confirm(`Replace ${active.name || "this signer"}'s fields with the suggested ones?`)) return;
      const n = await autoPlace(true, active.key); renderTagger(); ok(n ? `Placed ${n} fields for ${active.name}.` : "No suggestions for this signer.");
    }));
    if (correcting) top.append(button("Save corrections", saveCorrections, "button button-primary"));
    else {
      top.append(button(template ? "Save template" : "Save draft", async () => { await save(); editor.stage = "fields"; renderTagger(); ok("Saved."); }));
      if (!template) top.append(button("Send", send, "button button-primary"));
    }
    view.append(top);
    const tagger = el("div", "tagger");
    tagger.append(palette(active), pagesPane(), props(active));
    view.append(tagger);
    renderPages();
  }

  function palette(active) {
    const p = el("aside", "palette"); p.setAttribute("aria-label", "Fields");
    if (!active) { p.append(el("p", "muted small", "Add a recipient who needs to sign to place fields.")); return p; }
    p.style.setProperty("--who", color(active.key));
    const groups = [["Signing", ["signature", "initials", "date_signed"]], ["About the signer", ["name", "first_name", "last_name", "email", "company", "title"]],
      ["Input", ["text", "number", "date", "checkbox", "radio", "dropdown", "attachment", "note"]]];
    groups.forEach(([label, kinds]) => {
      p.append(el("h3", "", label));
      kinds.forEach(kind => {
        const b = el("button", `tool${editor.placing === kind ? " on" : ""}`); b.type = "button"; b.draggable = true;
        b.append(el("span", "tool-icon", ICON_TEXT[KINDS[kind][3]]), el("span", "", KINDS[kind][0]));
        b.addEventListener("click", () => { editor.placing = editor.placing === kind ? null : kind; renderTagger(); });
        b.addEventListener("dragstart", event => { event.dataTransfer.setData("text/x-field", kind); event.dataTransfer.effectAllowed = "copy"; });
        p.append(b);
      });
    });
    p.append(button("Initials on every page", async () => {
      const signers = editor.recipients.filter(r => SIGNING.includes(r.role) && !r.locked).sort((a, b) => a.routing_order - b.routing_order);
      const slot = Math.max(0, signers.findIndex(r => r.key === active.key));
      const d = await api(`/api/esign/envelopes/${editor.e.id}/suggestions?signers=${signers.length}`);
      let added = 0;
      editor.e.documents.filter(doc => doc.source !== "attachment").forEach(doc => (d.documents[doc.id]?.initials[slot] || []).forEach((spot, index) => {
        if (editor.fields.some(f => f.document_id === doc.id && f.page === index + 1 && f.kind === "initials" && f.recipient === active.key)) return;
        editor.fields.push({key: `f${++keySeq}`, recipient: active.key, document_id: doc.id, page: index + 1, kind: "initials", x: spot.x, y: spot.y, w: spot.w, h: spot.h, required: true, label: "", options: {}}); added++;
      }));
      dirty(); renderTagger(); ok(added ? `Added initials to ${added} page${added === 1 ? "" : "s"}. Drag any of them to adjust.` : "Every page already has initials for this signer.");
    }));
    p.append(el("p", "field-count", `${editor.fields.filter(f => f.recipient === active.key).length} fields for ${active.name || "this signer"}`));
    return p;
  }

  function pagesPane() { const pane = el("div", "pages"); pane.id = "esign-pages"; return pane; }

  function renderPages() {
    const container = $("esign-pages"); if (!container) return;
    container.replaceChildren();
    const wrap = el("div", "page-wrap");
    if (!editor.e.documents.length) { wrap.append(el("p", "empty-state", "Add a document first.")); container.append(wrap); return; }
    editor.e.documents.filter(d => d.source !== "attachment").forEach(d => {
      const title = el("div", "doc-title"); title.id = `doc-${d.id}`; title.append(el("span", "", d.name), el("span", "muted", `${d.pages} page${d.pages === 1 ? "" : "s"}`)); wrap.append(title);
      d.page_sizes.forEach((size, index) => {
        const number = index + 1;
        const page = el("div", "esign-page"); page.style.aspectRatio = `${size[0]} / ${size[1]}`;
        page.dataset.doc = d.id; page.dataset.page = number;
        page.classList.toggle("placing", !!editor.placing);
        const cacheKey = `${d.id}:${number}`;
        let image = images.get(cacheKey);
        if (!image) {
          image = el("img"); image.loading = number <= 2 ? "eager" : "lazy"; image.alt = `${d.name}, page ${number}`; image.draggable = false;
          image.src = `/api/esign/documents/${d.id}/page/${number}.png`; images.set(cacheKey, image);
        }
        page.append(image, el("span", "esign-page-number", `Page ${number} of ${d.pages}`));
        const place = (kind, clientX, clientY) => {
          if (!editor.active || lockedKey(editor.active)) return;
          const box = page.getBoundingClientRect();
          const scale = size[0] / box.width;
          const [, w, h] = KINDS[kind];
          const x = Math.min(Math.max(0, (clientX - box.left) * scale - w / 2), size[0] - w);
          const y = Math.min(Math.max(0, (clientY - box.top) * scale - h / 2), size[1] - h);
          const options = kind === "dropdown" ? {choices: ["Yes", "No"]} : kind === "radio" ? {group: "Choice", choice: `Option ${editor.fields.filter(f2 => f2.kind === "radio").length + 1}`}
            : kind === "note" ? {text: "Please read this before signing."} : {};
          const f = {key: `f${++keySeq}`, recipient: editor.active, document_id: d.id, page: number, kind, x: Math.round(x), y: Math.round(y), w, h,
            required: !["checkbox", "note", "radio"].includes(kind), label: "", options};
          editor.fields.push(f); editor.selected = f.key; dirty(); renderPages(); refreshProps();
        };
        page.addEventListener("click", event => { if (!editor.placing || event.target.closest(".esign-field")) return; place(editor.placing, event.clientX, event.clientY); });
        page.addEventListener("dragover", event => { if (event.dataTransfer.types.includes("text/x-field")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } });
        page.addEventListener("drop", event => { const kind = event.dataTransfer.getData("text/x-field"); if (KINDS[kind]) { event.preventDefault(); place(kind, event.clientX, event.clientY); } });
        editor.fields.filter(f => f.document_id === d.id && f.page === number).forEach(f => page.append(fieldNode(f, size)));
        wrap.append(page);
      });
    });
    container.append(wrap);
    const selected = container.querySelector(".esign-field.selected");
    if (selected && editor.scrollTo) { selected.scrollIntoView({block: "center"}); editor.scrollTo = false; }
  }

  function fieldText(f) { return f.kind === "radio" ? `${f.options?.choice || ""}` : f.kind === "note" ? `Note: ${f.options?.text || ""}` : f.label || KINDS[f.kind][0]; }
  function fieldNode(f, size) {
    const locked = lockedKey(f.recipient);
    const node = el("div", `esign-field${f.key === editor.selected ? " selected" : ""}${locked ? " locked" : ""}${f.options?.condition ? " conditional" : ""}`);
    node.dataset.key = f.key; node.tabIndex = 0;
    node.style.setProperty("--who", color(f.recipient));
    const place = () => { node.style.left = `${f.x / size[0] * 100}%`; node.style.top = `${f.y / size[1] * 100}%`; node.style.width = `${f.w / size[0] * 100}%`; node.style.height = `${f.h / size[1] * 100}%`; };
    place();
    if (f.w > 40) node.append(el("span", "kind-icon", ICON_TEXT[KINDS[f.kind][3]]));
    node.append(el("span", "esign-field-label", fieldText(f)));
    const handle = el("span", "esign-handle"); handle.setAttribute("aria-hidden", "true"); if (!locked) node.append(handle);
    node.setAttribute("aria-label", `${KINDS[f.kind][0]} for ${editor.recipients.find(r => r.key === f.recipient)?.name || "recipient"}`);
    node.title = `${KINDS[f.kind][0]} · ${editor.recipients.find(r => r.key === f.recipient)?.name || ""}${f.required ? " · required" : ""}`;
    node.addEventListener("pointerdown", event => {
      event.preventDefault(); event.stopPropagation();
      if (editor.selected !== f.key) { editor.selected = f.key; document.querySelectorAll(".esign-field.selected").forEach(n => n.classList.remove("selected")); node.classList.add("selected"); refreshProps(); }
      if (locked) return;
      const page = node.parentElement, box = page.getBoundingClientRect(), scale = size[0] / box.width;
      const resize = event.target === handle, start = [event.clientX, event.clientY, f.x, f.y, f.w, f.h];
      node.setPointerCapture(event.pointerId);
      const move = ev => {
        const dx = (ev.clientX - start[0]) * scale, dy = (ev.clientY - start[1]) * scale;
        if (resize) { f.w = Math.max(8, Math.min(size[0] - f.x, start[4] + dx)); f.h = Math.max(8, Math.min(size[1] - f.y, start[5] + dy)); }
        else { f.x = Math.max(0, Math.min(size[0] - f.w, start[2] + dx)); f.y = Math.max(0, Math.min(size[1] - f.h, start[3] + dy)); }
        f.x = Math.round(f.x * 10) / 10; f.y = Math.round(f.y * 10) / 10; f.w = Math.round(f.w * 10) / 10; f.h = Math.round(f.h * 10) / 10;
        place(); if (editor) editor.dirty = true;
      };
      const up = () => { node.removeEventListener("pointermove", move); node.removeEventListener("pointerup", up); node.removeEventListener("pointercancel", up); };
      node.addEventListener("pointermove", move); node.addEventListener("pointerup", up); node.addEventListener("pointercancel", up);
    });
    node.addEventListener("click", event => event.stopPropagation());
    return node;
  }
  function paintField(f) { const node = document.querySelector(`.esign-field[data-key="${f.key}"] .esign-field-label`); if (node) node.textContent = fieldText(f); }
  function refreshProps() { const old = document.querySelector(".props"); if (old) old.replaceWith(props(editor.recipients.find(r => r.key === editor.active && !r.locked))); }
  function removeField(f) { editor.fields = editor.fields.filter(x => x !== f); editor.selected = null; dirty(); renderPages(); refreshProps(); }
  function duplicate(f) {
    const doc = editor.e.documents.find(d => d.id === f.document_id), [, h] = doc.page_sizes[f.page - 1];
    const n = {...f, key: `f${++keySeq}`, y: Math.min(f.y + f.h + 8, h - f.h), options: {...f.options}};
    editor.fields.push(n); editor.selected = n.key; dirty(); renderPages(); refreshProps();
  }

  function props(active) {
    const p = el("aside", "props"); p.setAttribute("aria-label", "Field properties");
    const f = editor.fields.find(x => x.key === editor.selected);
    if (!f) {
      p.append(el("h3", "", "Fields"));
      p.append(el("p", "hint", editor.placing ? `Click on a page to place: ${KINDS[editor.placing][0]}. Press Esc to stop.` : "Drag a field from the left onto a page, or select it and click on the page. Select a placed field to change it; drag to move, drag the corner to resize, Delete removes it."));
      if (active) {
        const mine = editor.fields.filter(x => x.recipient === active.key);
        const counts = {}; mine.forEach(x => { counts[x.kind] = (counts[x.kind] || 0) + 1; });
        const ul = el("ul", "summary-list"); ul.style.marginTop = "12px";
        Object.entries(counts).forEach(([k, v]) => { const li = el("li"); li.append(el("span", "", KINDS[k][0]), el("strong", "", v)); ul.append(li); });
        if (!mine.some(x => x.kind === "signature")) p.append(el("p", "field-warning small", `${active.name || "This signer"} needs at least one signature field.`));
        p.append(ul);
      }
      const thumbs = el("div", "thumbs"); thumbs.append(el("h3", "", "Documents"));
      editor.e.documents.filter(d => d.source !== "attachment").forEach(d => {
        const b = el("button"); b.type = "button"; const img = el("img"); img.src = `/api/esign/documents/${d.id}/page/1.png`; img.alt = "";
        const t = el("span"); t.append(el("strong", "", d.name), el("br"), el("small", "muted", `${d.pages} pages · ${editor.fields.filter(x => x.document_id === d.id).length} fields`));
        b.append(img, t); b.addEventListener("click", () => $(`doc-${d.id}`)?.scrollIntoView({behavior: "smooth"})); thumbs.append(b);
      });
      p.append(thumbs);
      return p;
    }
    const head = el("div", "card-head"); head.append(el("h3", "", KINDS[f.kind][0]));
    if (!lockedKey(f.recipient)) { const tools = el("div"); tools.append(button("Copy", () => duplicate(f), "button button-quiet button-sm"), button("Delete", () => removeField(f), "button button-danger button-sm")); head.append(tools); }
    p.append(head);
    if (lockedKey(f.recipient)) { p.append(el("p", "hint", "This field belongs to someone who has finished; it cannot change.")); return p; }
    const o = f.options = f.options || {};
    const signers = editor.recipients.filter(r => SIGNING.includes(r.role) && !r.locked);
    const who = select(signers.map(r => [r.key, r.name || r.email || "Recipient"]), f.recipient);
    who.addEventListener("change", () => { f.recipient = who.value; delete o.condition; dirty(); renderPages(); refreshProps(); });
    p.append(field("Recipient", who));
    const check = (label, value, onChange) => { const c = input("", "checkbox", {checked: value}); c.addEventListener("change", () => { onChange(c.checked); dirty(); }); const l = el("label", "check-inline"); l.append(c, document.createTextNode(label)); l.style.marginBottom = "12px"; return l; };
    if (AUTO.includes(f.kind)) p.append(el("p", "hint", f.kind === "date_signed" ? "Filled in automatically with the date the recipient signs." : "Filled in automatically from the recipient's details."));
    else if (f.kind === "note") p.append(el("p", "hint", "Shown to the recipient while signing; not printed on the completed document."));
    else if (!o.readonly) p.append(check("Required", f.required, v => { f.required = v; }));
    if ([...TEXT_KINDS, "dropdown", "checkbox", "radio", "attachment"].includes(f.kind)) {
      const label = input(f.label, "text", {maxLength: 100, placeholder: KINDS[f.kind][0]}); label.addEventListener("input", () => { f.label = label.value; dirty(); paintField(f); });
      p.append(field(f.kind === "attachment" ? "What to attach (shown to the recipient)" : "Label shown to the recipient", label));
    }
    if (f.kind !== "note" && !AUTO.includes(f.kind)) {
      const tip = input(o.tooltip || "", "text", {maxLength: 200, placeholder: "Optional help text"}); tip.addEventListener("input", () => { o.tooltip = tip.value; dirty(); });
      p.append(field("Help text", tip));
    }
    if (f.kind === "note") {
      const text = Object.assign(el("textarea"), {rows: 4, maxLength: 1000, value: o.text || ""}); text.addEventListener("input", () => { o.text = text.value; dirty(); paintField(f); });
      p.append(field("Note to the recipient", text));
    }
    if (f.kind === "dropdown") {
      const choices = Object.assign(el("textarea"), {rows: 4, value: (o.choices || []).join("\n")});
      choices.addEventListener("input", () => { o.choices = choices.value.split("\n").map(x => x.trim()).filter(Boolean); dirty(); });
      p.append(field("Choices (one per line)", choices));
    }
    if (f.kind === "radio") {
      const group = input(o.group || "", "text", {maxLength: 60, placeholder: "e.g. Plan"}); group.addEventListener("input", () => { o.group = group.value.trim(); dirty(); });
      const value = input(o.choice || "", "text", {maxLength: 100, placeholder: "e.g. Monthly"}); value.addEventListener("input", () => { o.choice = value.value.trim(); dirty(); paintField(f); });
      p.append(field("Group (one choice per group)", group), field("Value of this button", value));
      p.append(button("Add another button to this group", () => {
        const n = {...f, key: `f${++keySeq}`, y: Math.min(f.y + 20, (editor.e.documents.find(d => d.id === f.document_id).page_sizes[f.page - 1][1]) - f.h), options: {...o, choice: ""}};
        editor.fields.push(n); editor.selected = n.key; dirty(); renderPages(); refreshProps();
      }));
    }
    if (TEXT_KINDS.includes(f.kind)) {
      if (!["number", "date"].includes(f.kind)) { const v = select(VALIDATION, o.validation || ""); v.addEventListener("change", () => { o.validation = v.value; dirty(); }); p.append(field("Accept", v)); }
      const max = input(o.max_length || "", "number", {min: 1, max: 4000, placeholder: "No limit"}); max.addEventListener("change", () => { o.max_length = Number(max.value) || undefined; dirty(); });
      p.append(field("Character limit", max));
      const prefill = input(o.prefill || "", "text", {maxLength: 500, placeholder: "Optional"}); prefill.addEventListener("input", () => { o.prefill = prefill.value; dirty(); });
      p.append(field("Value you fill in", prefill));
      p.append(check("Read-only: the recipient sees your value and cannot change it", !!o.readonly, v => { o.readonly = v; if (v) f.required = false; refreshProps(); }));
    }
    // Conditional logic: show this field only when a checkbox, radio group or dropdown of the same recipient has a value.
    const triggers = editor.fields.filter(x => x !== f && x.recipient === f.recipient && ["checkbox", "radio", "dropdown"].includes(x.kind));
    if (triggers.length) {
      const describe = x => x.kind === "radio" ? `Radio group ${x.options?.group || "(unnamed)"}` : `${KINDS[x.kind][0]}: ${x.label || "(no label)"} · page ${x.page}`;
      const seen = new Set(), unique = triggers.filter(x => { const id = x.kind === "radio" ? `r:${x.options?.group}` : x.key; if (seen.has(id)) return false; seen.add(id); return true; });
      const on = select([["", "Always show"], ...unique.map(x => [x.key, describe(x)])], o.condition?.field || "");
      const equals = el("select");
      const fillValues = () => {
        const t = editor.fields.find(x => x.key === on.value);
        equals.replaceChildren(); equals.hidden = !t;
        if (!t) return;
        const values = t.kind === "checkbox" ? [["checked", "is checked"], ["unchecked", "is not checked"]] : t.kind === "radio"
          ? editor.fields.filter(x => x.kind === "radio" && x.recipient === t.recipient && x.options?.group === t.options?.group).map(x => [x.options?.choice, `is ${x.options?.choice}`])
          : (t.options?.choices || []).map(c => [c, `is ${c}`]);
        values.forEach(([v, text]) => equals.append(new Option(text, v)));
        equals.value = o.condition?.equals && values.some(([v]) => v === o.condition.equals) ? o.condition.equals : values[0]?.[0] || "";
      };
      const store = () => { if (on.value) o.condition = {field: on.value, equals: equals.value}; else delete o.condition; dirty(); };
      on.addEventListener("change", () => { fillValues(); store(); }); equals.addEventListener("change", store);
      fillValues();
      p.append(field("Show this field", on), field("When it", equals));
    }
    return p;
  }

  document.addEventListener("keydown", event => {
    if (!editor || editor.stage !== "fields" || document.body.dataset.view !== "esign" || event.target.closest("input,textarea,select,[contenteditable]")) return;
    if (event.key === "Escape") { if (editor.placing) { editor.placing = null; renderTagger(); } else if (editor.selected) { editor.selected = null; renderPages(); refreshProps(); } return; }
    const f = editor.fields.find(x => x.key === editor.selected); if (!f || lockedKey(f.recipient)) return;
    if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); removeField(f); return; }
    const step = event.shiftKey ? 10 : 1, moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[event.key];
    if (moves) {
      event.preventDefault();
      const doc = editor.e.documents.find(d => d.id === f.document_id), [w, h] = doc.page_sizes[f.page - 1];
      f.x = Math.max(0, Math.min(w - f.w, f.x + moves[0])); f.y = Math.max(0, Math.min(h - f.h, f.y + moves[1])); dirty(); renderPages();
    }
  });

  function layoutBody() {
    const e = editor.e;
    return {
      recipients: editor.recipients.map(r => r.locked ? {id: r.key} : ({id: e.recipients.some(x => x.id === r.key) ? r.key : undefined, key: r.key, role: r.role, role_name: r.role_name,
        routing_order: e.settings.ordered ? r.routing_order : 1, name: r.name, email: r.email, note: r.note || "", access_code: r.access_code, host_id: r.role === "in_person" ? (r.host_id || user.id) : undefined})),
      fields: editor.fields.filter(f => !lockedKey(f.recipient)).map(f => ({key: f.key, recipient: f.recipient, document_id: f.document_id, page: f.page, kind: f.kind, x: f.x, y: f.y,
        w: f.w, h: f.h, required: f.required, label: f.label, options: f.options}))};
  }

  async function save(quiet) {
    const e = editor.e, stage = editor.stage, library = editor.library, active = editor.active;
    const body = {version: e.version, message: e.message, settings: e.settings, ...layoutBody()};
    if (e.status === "template") body.template_name = e.template_name; else { body.subject = e.subject; body.client_id = e.client_id || null; }
    const saved = await api(`/api/esign/envelopes/${e.id}/draft`, body);
    const shows = new Map(editor.recipients.map(r => [r.key, r.show]));
    await openEditor(saved.id, saved, false, stage);
    editor.library = library;
    editor.recipients.forEach(r => { r.show = shows.get(r.key) || r.show; });
    if (editor.recipients.some(r => r.key === active)) editor.active = active;
    if (!quiet) { editor.dirty = false; refreshAside(); }
    return saved;
  }

  function problems() {
    const out = setupProblems();
    editor.recipients.filter(r => !r.locked && SIGNING.includes(r.role)).forEach(r => {
      if (!editor.fields.some(f => f.recipient === r.key && f.kind === "signature")) out.push(`Add a signature field for ${r.name || "each signer"}.`);
    });
    editor.fields.filter(f => f.kind === "radio" && (!f.options?.group || !f.options?.choice)).forEach(() => out.push("Give every radio button a group and a value."));
    return [...new Set(out)];
  }

  async function send() {
    const found = problems();
    if (found.length) { fail(new Error(found.join(" "))); return; }
    await save(true);
    const result = await api(`/api/esign/envelopes/${editor.e.id}/send`, {});
    editor = null;
    ok("Sent. Each recipient receives an email from the company mailbox when it is their turn.");
    await showEnvelope(result.envelope.id);
  }

  async function saveCorrections() {
    const found = problems();
    if (found.length) { fail(new Error(found.join(" "))); return; }
    const result = await api(`/api/esign/envelopes/${editor.e.id}/correct`, {layout: layoutBody()});
    editor = null;
    ok("The envelope was corrected. Recipients who changed received a corrected email; new recipients get the envelope when it is their turn.");
    await showEnvelope(result.envelope.id);
  }

  // ------------------------------------------------------------------ company mailboxes
  async function showMailboxes() {
    const d = await api("/api/esign/mailboxes");
    const view = $("mailbox-view"); view.replaceChildren();
    view.append(pageHead("Admin", "Company mailboxes", "Each company sends signing requests, reminders and completed documents from its own compliance mailbox. Replies and bounce notices in that inbox are attached to the envelope automatically.",
      (() => { const a = el("a", "button button-quiet", "Email trust & verified sender"); a.href = "#/admin/email-trust"; return a; })()));
    const listEl = el("div", "company-cards");
    d.mailboxes.forEach(box => listEl.append(d.can_manage ? mailboxForm(box) : mailboxStatus(box)));
    view.append(listEl);
  }
  function state(box) { return box.mode === "off" ? ["Not connected", "draft"] : box.smtp_error || box.imap_error ? ["Connected, with a problem", "declined"] : box.ready ? ["Connected", "completed"] : ["Incomplete", "sent"]; }
  function mailboxStatus(box) {
    const card = el("section", "card");
    const [label, tone] = state(box);
    const head = el("div", "card-head"); head.append(el("h2", "", box.entity), chip(tone, label)); card.append(head, el("p", "", box.address || "No address set"));
    if (box.smtp_error) card.append(notice("danger", `Sending: ${box.smtp_error}`));
    if (box.imap_error) card.append(notice("danger", `Inbox: ${box.imap_error}`));
    return card;
  }
  function mailboxForm(box) {
    const card = el("section", "card");
    const [label, tone] = state(box);
    const head = el("div", "card-head"); head.append(el("h2", "", box.entity), chip(tone, label)); card.append(head);
    if (!box.secrets_available) card.append(notice("danger", "The server has no TFSC_SECRET_KEY, so passwords cannot be stored. Run sudo ./agreements.sh update on the server."));
    const form = el("form");
    const modes = [["mailbox", "Send from this company's own mailbox (recommended)"], ["relay", "Send through the platform's mail server; replies go to the mailbox"], ["off", "Not connected"]]
      .filter(([mode]) => mode !== "relay" || box.relay_available || box.mode === "relay");
    const mode = select(modes, box.mode); mode.name = "mode";
    const address = input(box.address, "email", {name: "address", maxLength: 200}), display = input(box.display_name, "text", {name: "display_name", maxLength: 150});
    form.append(field("How this company sends email", mode));
    const two = el("div", "settings-grid"); two.append(field("Mailbox address", address), field("Sender name shown to clients", display, "Use the company name, for example “Scribe Align LLC”. Gmail shows its first letter as the avatar unless the mailbox has a profile photo or BIMI logo.")); form.append(two);
    const own = el("div"); own.style.marginTop = "14px";
    const auth = select([["password", "App password"], ["google_sa", "Google Workspace service account"]], box.auth); auth.name = "auth";
    own.append(field("Sign-in method", auth));
    const secret = input("", "password", {name: "secret", maxLength: 500, autocomplete: "new-password", placeholder: box.has_secret ? "Saved. Enter a new one to replace it." : "16-character app password"});
    const passwordHelp = el("p", "field-note", "Google Workspace: sign in as this mailbox, turn on 2-Step Verification, then create an app password at myaccount.google.com/apppasswords and paste it here.");
    const sa = input("", "file", {name: "sa_key", accept: "application/json"});
    const saHelp = el("p", "field-note", box.has_sa_key ? `Key saved for ${box.sa_client_email}. Upload a new JSON key to replace it.` :
      "In the Google Admin console (Security › API controls › Domain-wide delegation), allow the service account's client ID the scope https://mail.google.com/, then upload its JSON key.");
    const passwordBlock = el("div"); passwordBlock.append(field("App password", secret), passwordHelp);
    const saBlock = el("div"); saBlock.append(field("Service account key (JSON)", sa), saHelp);
    own.append(passwordBlock, saBlock);
    const advanced = el("details"); advanced.append(el("summary", "", "Mail servers (Google Workspace is filled in)")); advanced.style.margin = "12px 0";
    const login = input(box.login, "text", {name: "login", maxLength: 200});
    let follow = box.login === box.address;
    address.addEventListener("input", () => { if (follow) login.value = address.value.trim(); });
    login.addEventListener("input", () => { follow = login.value.trim() === address.value.trim(); });
    const smtp = el("div", "settings-grid"); smtp.append(field("SMTP server", input(box.smtp_host, "text", {name: "smtp_host"})), field("Port", input(box.smtp_port, "number", {name: "smtp_port"})),
      field("Security", select([["starttls", "STARTTLS"], ["ssl", "SSL/TLS"], ["none", "None (test server only)"]], box.smtp_security)));
    smtp.lastChild.querySelector("select").name = "smtp_security";
    const imap = el("div", "settings-grid"); imap.append(field("IMAP server", input(box.imap_host, "text", {name: "imap_host"})), field("Port", input(box.imap_port, "number", {name: "imap_port"})),
      field("Security", select([["ssl", "SSL/TLS"], ["starttls", "STARTTLS"], ["none", "None (test server only)"]], box.imap_security)));
    imap.lastChild.querySelector("select").name = "imap_security";
    advanced.append(field("Sign-in name", login), smtp, imap);
    const inbox = input("", "checkbox", {name: "imap_enabled", checked: box.imap_enabled}); const inboxLabel = el("label", "check-inline"); inboxLabel.append(inbox, document.createTextNode("Read replies and bounce notices from this inbox (read-only; nothing is changed in the mailbox)"));
    own.append(inboxLabel, advanced);
    form.append(own);
    form.append(field("Company signer's email (used when the company signs electronically)", input(box.signer_email, "email", {name: "signer_email", maxLength: 200})));
    const notify = el("fieldset"); notify.style.border = "1px solid var(--line)"; notify.style.borderRadius = "10px"; notify.style.padding = "12px 16px";
    notify.append(el("legend", "", "Email the sender and this mailbox when a recipient…"));
    [["viewed", "views the documents"], ["signed", "signs"], ["completed", "everyone has finished (with the documents attached)"], ["declined", "declines"], ["bounced", "an email bounces"],
      ["replied", "replies by email (to the sender)"], ["auth_failed", "is locked out by wrong access codes"], ["reassigned", "assigns the documents to someone else"],
      ["voided", "the envelope is voided"], ["expired", "the envelope expires"]].forEach(([key, text]) => {
      const c = input(key, "checkbox", {checked: box.notify.includes(key), name: "notify"}); const l = el("label", "check-inline"); l.append(c, document.createTextNode(text)); notify.append(l);
    });
    form.append(notify);
    const sync = () => { own.hidden = mode.value !== "mailbox"; passwordBlock.hidden = auth.value !== "password"; saBlock.hidden = auth.value !== "google_sa"; };
    mode.addEventListener("change", sync); auth.addEventListener("change", sync); sync();
    const result = el("p", "muted"); result.setAttribute("role", "status");
    const actions = el("div", "page-actions"); actions.style.marginTop = "16px";
    actions.append(button("Save", async () => {
      const get = name => form.querySelector(`[name="${name}"]`);
      const payload = {mode: mode.value, address: address.value.trim(), display_name: display.value.trim(), auth: auth.value, login: get("login").value.trim(),
        smtp_host: get("smtp_host").value.trim(), smtp_port: Number(get("smtp_port").value), smtp_security: get("smtp_security").value,
        imap_host: get("imap_host").value.trim(), imap_port: Number(get("imap_port").value), imap_security: get("imap_security").value, imap_enabled: inbox.checked,
        signer_email: get("signer_email").value.trim(), notify: [...form.querySelectorAll('[name="notify"]:checked')].map(c => c.value)};
      if (secret.value) payload.secret = secret.value;
      if (sa.files[0]) payload.sa_key = await sa.files[0].text();
      await api(`/api/esign/mailboxes/${box.index}`, payload);
      secret.value = ""; ok(`${box.short} mailbox saved.`); await showMailboxes();
    }, "button button-primary"));
    const to = input(user.email, "email", {placeholder: "Send a test to"}); to.setAttribute("aria-label", "Send a test email to"); to.style.maxWidth = "260px";
    actions.append(to, button("Send test email", async () => { result.textContent = "Sending…"; const r = await api(`/api/esign/mailboxes/${box.index}/test`, {what: "send", to: to.value}); result.textContent = r.message; result.className = r.ok ? "notice notice-success" : "notice notice-danger"; }));
    actions.append(button("Check inbox", async () => { result.textContent = "Signing in to the inbox…"; const r = await api(`/api/esign/mailboxes/${box.index}/test`, {what: "inbox"}); result.textContent = r.message; result.className = r.ok ? "notice notice-success" : "notice notice-danger"; }));
    const status = el("div");
    if (box.smtp_error) status.append(notice("danger", `Last sending problem: ${box.smtp_error}`));
    if (box.imap_error) status.append(notice("danger", `Last inbox problem: ${box.imap_error}`));
    if (box.imap_last_poll && !box.imap_error && box.mode === "mailbox" && box.imap_enabled) status.append(el("p", "muted small", `Inbox last checked ${when(box.imap_last_poll)}.`));
    Object.values(box.last_test || {}).forEach(t => status.append(el("p", `small ${t.ok ? "muted" : "attention-flag"}`, `${t.what === "inbox" ? "Inbox check" : "Test email"} ${when(t.at)}: ${t.message}`)));
    card.append(form, actions, result, status);
    return card;
  }

  // ------------------------------------------------------------------ bulk send
  function parseCsv(text) {
    const rows = []; let row = [], cell = "", quoted = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (quoted) { if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (ch === '"') quoted = false; else cell += ch; }
      else if (ch === '"') quoted = true;
      else if (ch === "," || ch === "\t" || ch === ";") { row.push(cell.trim()); cell = ""; }
      else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cell.trim()); cell = ""; if (row.some(Boolean)) rows.push(row); row = []; }
      else cell += ch;
    }
    row.push(cell.trim()); if (row.some(Boolean)) rows.push(row);
    let columns = {name: 0, email: 1, client: 2, note: 3};
    if (rows.length && rows[0].some(c => /e-?mail/i.test(c))) {
      const header = rows.shift().map(c => c.toLowerCase());
      const find = re => header.findIndex(c => re.test(c));
      columns = {name: find(/name/), email: find(/e-?mail/), client: find(/client|practice|company|organi[sz]ation/), note: find(/note|message/)};
    }
    return rows.map(r => ({name: r[columns.name] || "", email: r[columns.email] || "", client: columns.client >= 0 ? r[columns.client] || "" : "", note: columns.note >= 0 ? r[columns.note] || "" : ""}));
  }

  async function showBulk() {
    if (!guard()) return;
    editor = null; document.body.classList.remove("tagging");
    window.portalView?.("esign");
    const [templates, batches] = await Promise.all([api("/api/esign/envelopes?box=templates"), api("/api/esign/bulk")]);
    const view = root(); view.replaceChildren();
    view.append(backLink("Agreements", () => showList()));
    view.append(pageHead("Agreements", "Bulk send", "Send one template to many people: each person gets their own envelope and signs separately. The template's first signer is replaced by each person in your list; everyone else in the template and every field stay the same."));
    if (!templates.envelopes.length) { const empty = el("div", "empty-state"); empty.append(el("strong", "", "Save a template first"), el("p", "", "Open an envelope and choose More › Save as template.")); view.append(empty); }
    else {
      const form = el("section", "card");
      const template = select(templates.envelopes.map(t => [t.id, `${t.template_name} · ${(entities.find(x => x.name === t.entity) || {}).short || t.entity}`]));
      const name = input("", "text", {maxLength: 200, placeholder: "Optional, e.g. November BAA renewals"});
      const text = Object.assign(el("textarea"), {rows: 8, placeholder: "One person per line: Name, Email, Client (optional), Private message (optional)\nPat Doctor, pat@clinic.com, Northside Family Clinic\n\nOr upload a CSV file with a header row (Name, Email, Client, Note)."});
      const file = input("", "file", {accept: ".csv,text/csv,text/plain"});
      const preview = el("div");
      const render = () => {
        const rows = parseCsv(text.value);
        const bad = rows.filter(r => !r.name || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.email));
        preview.replaceChildren(el("p", bad.length ? "field-warning small" : "muted small", `${rows.length} recipient${rows.length === 1 ? "" : "s"}${bad.length ? `, ${bad.length} with a missing name or an invalid email` : ""}.`));
        if (rows.length) {
          const t = el("table", "portal-table"); const h = el("tr"); ["#", "Name", "Email", "Client", "Private message"].forEach(x => h.append(el("th", "", x)));
          const th = el("thead"); th.append(h); t.append(th); const tb = el("tbody");
          rows.slice(0, 200).forEach((r, i) => { const tr = el("tr"); [i + 1, r.name, r.email, r.client, r.note].forEach(x => tr.append(el("td", "", x))); tb.append(tr); });
          t.append(tb); const wrap = el("div"); wrap.style.overflow = "auto"; wrap.append(t); preview.append(wrap);
        }
        return rows;
      };
      text.addEventListener("input", render);
      file.addEventListener("change", async () => { if (file.files[0]) { text.value = await file.files[0].text(); render(); } });
      const result = el("div");
      const grid = el("div", "settings-grid"); grid.append(field("Template", template), field("Batch name", name));
      form.append(grid, field("Recipients", text), field("Or upload a CSV file", file), preview,
        button("Send to everyone", async () => {
          const rows = render();
          if (!rows.length) throw new Error("Add at least one recipient.");
          if (!confirm(`Send ${rows.length} envelope${rows.length === 1 ? "" : "s"} now?`)) return;
          ok("Sending…");
          const d = await api("/api/esign/bulk", {template_id: template.value, name: name.value.trim(), rows});
          ok(`Bulk send finished: ${d.sent} sent${d.failed ? `, ${d.failed} not sent` : ""}.`);
          result.replaceChildren(batchTable(d));
        }, "button button-primary"), result);
      view.append(form);
    }
    if (batches.batches.length) {
      view.append(el("h2", "", "Earlier bulk sends"));
      batches.batches.forEach(b => {
        const d = el("details", "card"); d.style.marginBottom = "10px"; d.append(el("summary", "", `${b.name} · ${when(b.created)} · ${b.sent} sent${b.failed ? `, ${b.failed} not sent` : ""}`), batchTable(b));
        view.append(d);
      });
    }
  }
  function batchTable(b) {
    const t = el("table", "portal-table"); const h = el("tr"); ["#", "Recipient", "Result"].forEach(x => h.append(el("th", "", x)));
    const th = el("thead"); th.append(h); t.append(th); const tb = el("tbody");
    b.results.forEach(r => {
      const tr = el("tr"); const who = el("td"); who.append(el("strong", "", r.name), el("small", "", r.email));
      const res = el("td"); if (r.error) res.append(el("span", "attention-flag", r.error)); else res.append(button(`Sent · ${r.ref}`, () => showEnvelope(r.envelope), "text-button"));
      tr.append(el("td", "", r.row), who, res); tb.append(tr);
    });
    t.append(tb); return t;
  }
  window.esignBulk = () => go(showBulk).catch(fail);
  window.esignReports = () => window.portalRoute?.("#/reports");

  // ------------------------------------------------------------------ start-up
  window.addEventListener("beforeunload", event => { if (editor?.dirty) { event.preventDefault(); event.returnValue = ""; } });
  window.esignReady = config => {
    user = config.user; entities = config.entities || [];
    api("/api/esign/envelopes?box=action").then(d => badge(d.counts.action)).catch(() => {});
  };
  window.esignLeave = () => { if (!guard()) return false; editor = null; document.body.classList.remove("tagging"); return true; };
})();
