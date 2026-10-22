/* Electronic signature for senders: envelopes, the field editor, envelope details and company mailboxes. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const root = () => $("esign-view");
  const COLORS = ["#f5b400", "#2a8ff0", "#e5547a", "#2fae7f", "#8e6cf0", "#f0863c", "#3bb6c4", "#9a9a9a"];
  const STATUS = {draft: "Draft", sent: "Waiting for others", completing: "Finishing", completed: "Completed", declined: "Declined", voided: "Voided", expired: "Expired", template: "Template"};
  const R_STATUS = {created: "Waiting for their turn", sent: "Sent", delivered: "Viewed", signed: "Signed", completed: "Completed", declined: "Declined", auth_failed: "Locked: wrong access code"};
  const ROLES = {signer: "Needs to sign", viewer: "Needs to view", cc: "Receives a copy"};
  const KINDS = {signature: ["Signature", 170, 42], initials: ["Initials", 64, 24], date_signed: ["Date signed", 110, 16], name: ["Full name", 150, 16], email: ["Email", 170, 16],
    company: ["Company", 150, 16], title: ["Title", 150, 16], text: ["Text", 150, 16], checkbox: ["Checkbox", 14, 14], dropdown: ["Dropdown", 150, 16]};
  const AUTO = ["date_signed", "name", "email"];
  const BOXES = [["action", "Action required"], ["waiting", "Waiting for others"], ["completed", "Completed"], ["drafts", "Drafts"], ["declined", "Declined"], ["voided", "Voided"], ["expired", "Expired"], ["templates", "Templates"]];
  const EMAIL_KINDS = {invite: "Signing request", reminder: "Reminder", expiring: "Expiration warning", corrected: "Corrected request", copy: "Copy", completed: "Completed copy", voided: "Voided notice", declined: "Declined notice"};
  let user = null, entities = [];
  const list = {box: "waiting", q: "", page: 0};
  let editor = null;
  const images = new Map();  // page images reused across editor renders, so nothing is downloaded twice

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function button(text, fn, cls = "button button-quiet") { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", () => Promise.resolve().then(fn).catch(fail)); return b; }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : ""; }
  function fail(e) { if (!e || !e.message) return; const box = $("app-error"); box.textContent = e.message; box.hidden = false; box.scrollIntoView({behavior: "smooth", block: "center"}); }
  function ok(text) { const box = $("status-message"); box.textContent = text; box.hidden = false; $("app-error").hidden = true; }
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
  function chip(status, label) { return el("span", `esign-chip esign-${status}`, label || STATUS[status] || status); }
  function go(fn) { clear(); window.portalView?.("esign"); return fn(); }
  function context(text) { const node = document.querySelector(".header-context"); if (node) node.textContent = text; }
  function guard() { return !editor || !editor.dirty || confirm("Leave the editor without saving your changes?"); }

  // ------------------------------------------------------------------ envelope lists
  async function showList() {
    if (!guard()) return;
    editor = null; context("Signatures");
    const d = await api(`/api/esign/envelopes?box=${list.box}&q=${encodeURIComponent(list.q)}&page=${list.page}`);
    const view = root(); view.replaceChildren();
    const head = el("div", "history-toolbar");
    const title = el("div"); title.append(el("p", "eyebrow", "ELECTRONIC SIGNATURE"), el("h2", "", "Signatures"));
    head.append(title, button("New envelope", newEnvelope, "button button-primary"));
    view.append(head, el("p", "portal-note", "Send agreements for signature, follow every recipient, and download completed copies with their Certificate of Completion."));
    const tabs = el("div", "esign-boxes"); tabs.setAttribute("role", "tablist");
    BOXES.forEach(([key, label]) => {
      const count = d.counts[key];
      const tab = button(count ? `${label} (${count})` : label, () => { list.box = key; list.page = 0; return showList(); }, "esign-box");
      tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", String(key === list.box));
      tabs.append(tab);
    });
    const search = el("form", "portal-toolbar");
    const input = el("input"); input.type = "search"; input.placeholder = "Search subject, envelope ID or recipient"; input.value = list.q; input.setAttribute("aria-label", "Search envelopes");
    search.append(input, el("button", "button button-primary", "Search"));
    search.addEventListener("submit", event => { event.preventDefault(); list.q = input.value.trim(); list.page = 0; showList().catch(fail); });
    const rows = el("div", "esign-list");
    if (!d.envelopes.length) rows.append(el("p", "empty-state", list.box === "action" ? "Nothing is waiting for your signature." : "No envelopes here yet."));
    d.envelopes.forEach(e => rows.append(row(e)));
    const paging = el("div", "portal-paging");
    const back = button("Previous", () => { list.page--; return showList(); }), more = button("Next", () => { list.page++; return showList(); });
    back.disabled = list.page === 0; more.disabled = !d.has_more;
    paging.append(back, el("span", "", `Page ${list.page + 1}`), more);
    view.append(tabs, search, rows, paging);
    badge(d.counts.action);
  }

  function badge(count) { const b = $("esign-count"); if (!b) return; b.textContent = String(count || ""); b.hidden = !count; }

  function row(e) {
    const item = el("article", "esign-row");
    const main = el("button", "esign-row-main"); main.type = "button";
    main.addEventListener("click", () => (e.status === "draft" || e.status === "template" ? openEditor(e.id) : showEnvelope(e.id)).catch(fail));
    const left = el("div");
    left.append(el("strong", "", e.status === "template" ? e.template_name : e.subject || "(no subject)"));
    const people = e.recipients.filter(p => p.role !== "cc").map(p => p.name || p.email).join(", ");
    left.append(el("small", "", people ? `To: ${people}` : "No recipients yet"));
    left.append(el("small", "esign-meta", [e.ref, e.entity, e.client].filter(Boolean).join(" · ")));
    const middle = el("div", "esign-row-status");
    middle.append(chip(e.status));
    if (e.status === "sent" || e.status === "completing") middle.append(el("small", "", `${e.progress[0]} of ${e.progress[1]} done`));
    if (e.attention) middle.append(el("small", "esign-attention", "Needs attention"));
    const right = el("small", "esign-when", e.completed_at ? `Completed ${when(e.completed_at)}` : e.sent_at ? `Sent ${when(e.sent_at)}` : `Updated ${when(e.updated)}`);
    main.append(left, middle, right);
    item.append(main);
    if (list.box === "action") item.append(button("Sign", () => signNow(e.id), "button button-primary"));
    if (e.status === "template") item.append(button("Use", () => useTemplate(e), "button button-quiet"));
    return item;
  }

  async function signNow(id) { const d = await api(`/api/esign/envelopes/${id}/sign-now`, {}); location.href = d.url; }

  function dialog(title, build, confirmText, onConfirm) {
    const box = el("dialog", "portal-dialog esign-dialog");
    const form = el("form", "dialog-body"); form.method = "dialog";
    const head = el("div", "dialog-heading"); head.append(el("h2", "", title));
    const close = el("button", "icon-button", "×"); close.type = "button"; close.setAttribute("aria-label", "Close"); close.addEventListener("click", () => box.close());
    head.append(close);
    const message = el("p", "esign-dialog-error"); message.hidden = true;
    const confirmButton = el("button", "button button-primary", confirmText); confirmButton.type = "submit";
    build(form);
    form.append(message, confirmButton);
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
  function field(label, input) { const wrap = el("div", "field"); const l = el("label", "", label); input.id = input.id || `esign-${Math.random().toString(36).slice(2)}`; l.htmlFor = input.id; wrap.append(l, input); return wrap; }
  function select(options, value) { const s = el("select"); options.forEach(([v, t]) => s.append(new Option(t, v))); s.value = value ?? options[0]?.[0] ?? ""; return s; }
  function input(value = "", type = "text", attrs = {}) { const i = el("input"); i.type = type; i.value = value ?? ""; Object.entries(attrs).forEach(([k, v]) => { i[k] = v; }); return i; }

  async function clientOptions() {
    const d = await api("/api/clients");
    return [["", "No client (or choose later)"], ...d.clients.map(c => [c.id, c.client_name])];
  }

  async function newEnvelope() {
    const clients = await clientOptions();
    dialog("New envelope", form => {
      form.append(el("p", "portal-note", "Start an envelope with your own PDF documents. To send generated agreements, use Send for signature on the agreement after generating it."));
      form.append(field("Company sending the documents", select(entities.map(e => [e.name, e.name]))));
      form.append(field("Client (optional)", select(clients)));
    }, "Create draft", async form => {
      const [company, client] = form.querySelectorAll("select");
      const env = await api("/api/esign/envelopes", {entity: company.value, client_id: client.value || null});
      await openEditor(env.id, env);
    });
  }

  async function useTemplate(t) {
    const clients = await clientOptions();
    dialog(`Use template: ${t.template_name}`, form => form.append(field("Client (optional)", select(clients))), "Create draft", async form => {
      const env = await api(`/api/esign/envelopes/${t.id}/copy`, {client_id: form.querySelector("select").value || null});
      await openEditor(env.id, env);
    });
  }

  window.esignFromContract = async contractId => {
    if (!guard()) return;
    try {
      const env = await api("/api/esign/envelopes", {contract_id: contractId});
      await go(() => openEditor(env.id, env));
      ok("Draft envelope created. The client's signature, name, title and date fields were placed on each agreement. Check the recipients, then select Send.");
    } catch (e) { fail(e); }
  };
  window.esignOpen = id => go(() => showEnvelope(id)).catch(fail);
  window.esignShowList = () => go(showList).catch(fail);
  window.esignShowMailboxes = () => { clear(); window.portalView?.("mailboxes"); showMailboxes().catch(fail); };

  // ------------------------------------------------------------------ envelope details
  async function showEnvelope(id) {
    if (!guard()) return;
    editor = null;
    const e = await api(`/api/esign/envelopes/${id}`);
    if (e.status === "draft" || e.status === "template") return openEditor(id, e);
    context("Signatures");
    const view = root(); view.replaceChildren();
    view.append(button("← Signatures", showList, "text-button esign-back"));
    const head = el("div", "esign-detail-head");
    const title = el("div"); title.append(el("p", "eyebrow", `ENVELOPE ${e.ref}`), el("h2", "", e.subject));
    const facts = el("p", "esign-meta", [e.entity, e.client, `Sent by ${e.sender.name}`, e.sent_at && `sent ${when(e.sent_at)}`,
      e.status === "sent" && e.expires_at && `expires ${when(e.expires_at)}`, e.completed_at && `completed ${when(e.completed_at)}`].filter(Boolean).join(" · "));
    title.append(facts);
    head.append(title, chip(e.status));
    view.append(head);
    if (e.close_reason && ["declined", "voided"].includes(e.status)) view.append(el("p", "esign-alert", `${e.status === "declined" ? "Declined" : "Voided"}: ${e.close_reason}`));
    if (e.status === "completing" && e.completion_error) view.append(el("p", "esign-alert", `Finishing is being retried: ${e.completion_error}`));
    if (!e.mailbox.ready && e.status === "sent") view.append(el("p", "esign-alert", `The ${e.entity_short} mailbox is not connected, so emails cannot be sent. A Super Admin can connect it under Company mailboxes.`));
    const actions = el("div", "esign-actions");
    if (e.can_sign_now) actions.append(button("Sign now", () => signNow(e.id), "button button-primary"));
    if (e.status === "sent") {
      actions.append(button("Resend", async () => { await api(`/api/esign/envelopes/${e.id}/resend`, {}); ok("The signing request was sent again to everyone who has not finished."); await showEnvelope(e.id); }));
      actions.append(button("Correct", () => correct(e)));
      actions.append(button("Void", () => voidEnvelope(e), "button button-quiet esign-danger"));
    }
    if (e.status === "completed") {
      actions.append(link(`/api/esign/envelopes/${e.id}/combined`, "Download completed documents", "button button-primary"));
      actions.append(link(`/api/esign/envelopes/${e.id}/certificate`, "Certificate of Completion", "button button-quiet"));
    }
    actions.append(button("Copy", async () => { const copy = await api(`/api/esign/envelopes/${e.id}/copy`, {}); await openEditor(copy.id, copy); }));
    actions.append(button("Save as template", () => saveTemplate(e)));
    view.append(actions);

    view.append(el("h3", "", "Recipients"));
    const table = el("table", "portal-table esign-table");
    const thead = el("thead"); const hr = el("tr");
    ["Order", "Recipient", "Action", "Status", "Email delivery"].forEach(t => hr.append(el("th", "", t)));
    thead.append(hr); table.append(thead);
    const body = el("tbody");
    e.recipients.forEach(r => {
      const tr = el("tr");
      const who = el("td"); who.append(el("strong", "", r.name), el("small", "", r.email));
      if (r.has_access_code) who.append(el("small", "", "Access code required"));
      const status = el("td"); status.append(chip(r.status === "auth_failed" ? "declined" : r.status === "signed" || r.status === "completed" ? "completed" : r.status === "declined" ? "declined" : "sent", R_STATUS[r.status]));
      const times = [r.sent_at && `Sent ${when(r.sent_at)}`, r.delivered_at && `Viewed ${when(r.delivered_at)}`, r.signed_at && `${r.role === "signer" ? "Signed" : "Done"} ${when(r.signed_at)}`,
        r.declined_at && `Declined ${when(r.declined_at)}: ${r.decline_reason}`].filter(Boolean);
      times.forEach(t => status.append(el("small", "", t)));
      const delivery = el("td");
      if (r.delivery === "bounced" || r.delivery === "failed") { delivery.append(el("strong", "esign-attention", r.delivery === "bounced" ? "Undeliverable" : "Not sent"), el("small", "", r.delivery_error || "")); }
      else delivery.append(el("span", "", r.delivery === "sent" ? "Delivered to the mail server" : r.status === "created" ? "Not sent yet" : "Sending…"));
      if (r.reminders_sent) delivery.append(el("small", "", `${r.reminders_sent} reminder${r.reminders_sent === 1 ? "" : "s"}`));
      tr.append(el("td", "", r.routing_order), who, el("td", "", ROLES[r.role]), status, delivery);
      body.append(tr);
    });
    table.append(body); view.append(table);

    view.append(el("h3", "", "Documents"));
    const docs = el("div", "esign-docs");
    e.documents.forEach(d => {
      const card = el("div", "esign-doc");
      card.append(el("strong", "", d.name), el("small", "", `${d.pages} page${d.pages === 1 ? "" : "s"}`));
      const links = el("div", "file-links");
      links.append(link(`/api/esign/documents/${d.id}/file`, "Original PDF", ""));
      if (d.final_sha256) links.append(link(`/api/esign/documents/${d.id}/file?final=1`, "Completed PDF", ""));
      card.append(links); docs.append(card);
    });
    view.append(docs);

    if (e.replies.length) {
      view.append(el("h3", "", "Email replies and bounces"));
      e.replies.forEach(m => {
        const card = el("article", `esign-reply esign-reply-${m.kind}`);
        card.append(el("strong", "", m.kind === "bounce" ? `Undeliverable notice · ${when(m.received)}` : `${m.from_addr} · ${when(m.received)}`));
        card.append(el("small", "", m.subject));
        card.append(el("p", "esign-snippet", m.snippet || "(no text)"));
        if (m.files.length) {
          const files = el("div", "file-links");
          m.files.forEach(f => { const a = link(`/api/esign/attachments/${f.id}`, `${f.name} (${Math.ceil(f.size / 1024)} KB${f.scan === "clean" ? ", virus-scanned" : ", not scanned"})`, ""); files.append(a); });
          card.append(files);
        }
        view.append(card);
      });
    }

    view.append(el("h3", "", "History"));
    view.append(el("p", e.chain_ok ? "esign-integrity" : "esign-alert", e.chain_ok ? "Audit trail verified: every event is intact and in order." : "Warning: the audit trail does not verify. An event was changed or removed outside the application."));
    const history = el("ol", "esign-history");
    [...e.history].reverse().forEach(h => {
      const item = el("li");
      item.append(el("strong", "", h.label + (h.recipient && !h.label.includes(h.recipient) ? ` · ${h.recipient}` : "")));
      const details = [when(h.at), h.actor, h.ip && `IP ${h.ip}`, h.detail?.to && `to ${h.detail.to}`, h.detail?.reason && `reason: ${h.detail.reason}`,
        h.detail?.from && `from ${h.detail.from}`, h.detail?.attachments?.length && `attachments: ${h.detail.attachments.join(", ")}`].filter(Boolean);
      item.append(el("small", "", details.join(" · ")));
      history.append(item);
    });
    view.append(history);

    if (e.mails.length) {
      view.append(el("h3", "", "Emails"));
      const mails = el("table", "portal-table esign-table");
      const h = el("tr"); ["Email", "To", "Status", "Time"].forEach(t => h.append(el("th", "", t))); const mh = el("thead"); mh.append(h); mails.append(mh);
      const mb = el("tbody");
      e.mails.forEach(m => {
        const tr = el("tr");
        const state = {queued: m.attempts ? `Retrying (attempt ${m.attempts + 1})` : "Queued", sending: "Sending", sent: "Sent", failed: "Failed", bounced: "Undeliverable"}[m.state] || m.state;
        const status = el("td"); status.append(el("span", m.state === "failed" || m.state === "bounced" ? "esign-attention" : "", state));
        if (m.last_error && m.state !== "sent") status.append(el("small", "", m.last_error));
        const to = el("td", "", m.to_addrs.concat(m.cc_addrs).join(", ")); if (m.bcc_addrs.length) to.append(el("small", "", `Copy to ${m.bcc_addrs.join(", ")}`));
        tr.append(el("td", "", EMAIL_KINDS[m.kind] || m.kind.replace("notify_", "Notification: ")), to, status, el("td", "", when(m.sent_at || m.created)));
        mb.append(tr);
      });
      mails.append(mb); view.append(mails);
    }
  }
  function link(href, text, cls) { const a = el("a", cls, text); a.href = href; a.setAttribute("download", ""); return a; }

  function correct(e) {
    const open = e.recipients.filter(r => !["signed", "completed", "declined"].includes(r.status));
    if (!open.length) { fail(new Error("Everyone has finished; there is nothing to correct.")); return; }
    dialog("Correct envelope", form => {
      form.append(el("p", "portal-note", "Change the details of recipients who have not finished. Anyone whose email or access code changes gets a new email; earlier links stop working."));
      open.forEach(r => {
        const box = el("fieldset", "esign-correct"); box.dataset.id = r.id;
        box.append(el("legend", "", `${r.name} · ${ROLES[r.role]}`));
        box.append(field("Name", input(r.name, "text", {maxLength: 150, name: "name"})), field("Email", input(r.email, "email", {maxLength: 200, name: "email"})));
        box.append(field(r.has_access_code ? "New access code (leave blank to keep it)" : "Access code (optional)", input("", "text", {maxLength: 50, name: "access_code", autocomplete: "off"})));
        if (r.has_access_code) { const l = el("label", "check-inline"); const c = input("", "checkbox", {name: "remove_code"}); l.append(c, document.createTextNode("Remove the access code")); box.append(l); }
        if (r.status === "auth_failed") { const l = el("label", "check-inline"); const c = input("", "checkbox", {name: "unlock", checked: true}); l.append(c, document.createTextNode("Unlock access")); box.append(l); }
        box.append(field("Private message", Object.assign(el("textarea"), {value: r.note, maxLength: 1000, name: "note", rows: 2})));
        form.append(box);
      });
      form.append(field("Extend expiration to this many days from today (optional)", input("", "number", {min: 1, max: 365, name: "expire_days"})));
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
      await api(`/api/esign/envelopes/${e.id}/correct`, {recipients, expire_days: days ? Number(days) : null});
      ok("The envelope was corrected.");
      await showEnvelope(e.id);
    });
  }

  function voidEnvelope(e) {
    dialog("Void envelope", form => {
      form.append(el("p", "portal-note", "Voiding cancels the envelope. Recipients who already received it are told, with your reason. This cannot be undone."));
      form.append(field("Reason", Object.assign(el("textarea"), {maxLength: 500, rows: 3, required: true})));
    }, "Void envelope", async form => {
      await api(`/api/esign/envelopes/${e.id}/void`, {reason: form.querySelector("textarea").value});
      ok("The envelope was voided.");
      await showEnvelope(e.id);
    });
  }

  function saveTemplate(e) {
    dialog("Save as template", form => form.append(field("Template name", input(e.subject.slice(0, 150), "text", {maxLength: 150, required: true}))), "Save template", async form => {
      await api(`/api/esign/envelopes/${e.id}/copy`, {template_name: form.querySelector("input").value});
      ok("Template saved. Find it under Signatures > Templates.");
    });
  }

  // ------------------------------------------------------------------ editor (drafts and templates)
  let keySeq = 0;
  async function openEditor(id, data) {
    const e = data || await api(`/api/esign/envelopes/${id}`);
    editor = {e, recipients: e.recipients.map(r => ({...r, key: r.id, access_code: null})), fields: e.fields.map(f => ({...f, recipient: f.recipient_id, key: `f${++keySeq}`})),
      active: null, selected: null, placing: null, dirty: false};
    editor.active = editor.recipients.find(r => r.role === "signer")?.key || editor.recipients[0]?.key || null;
    renderEditor();
  }
  function color(key) { const index = editor.recipients.findIndex(r => r.key === key); return COLORS[(index < 0 ? 7 : index) % COLORS.length]; }
  function dirty() { if (editor) editor.dirty = true; }

  function renderEditor() {
    const e = editor.e, template = e.status === "template";
    context(template ? "Template" : "Prepare envelope");
    const view = root(); view.replaceChildren();
    const top = el("div", "esign-editor-top");
    top.append(button("← Signatures", showList, "text-button esign-back"));
    const subject = input(template ? e.template_name : e.subject, "text", {maxLength: template ? 150 : 200, placeholder: template ? "Template name" : "Email subject"});
    subject.className = "esign-subject"; subject.setAttribute("aria-label", template ? "Template name" : "Email subject");
    subject.addEventListener("input", () => { if (template) e.template_name = subject.value; else e.subject = subject.value; dirty(); });
    const right = el("div", "esign-actions");
    right.append(button("Delete", async () => {
      if (!confirm(template ? "Delete this template?" : "Delete this draft envelope?")) return;
      await api(`/api/esign/envelopes/${e.id}/delete`, {}); editor = null; ok(template ? "Template deleted." : "Draft deleted."); await showList();
    }, "button button-quiet esign-danger"));
    right.append(button(template ? "Save template" : "Save draft", async () => { await save(); ok("Saved."); }));
    if (!template) right.append(button("Send", send, "button button-primary"));
    top.append(subject, right);
    view.append(top);
    const facts = el("p", "esign-meta", [`ENVELOPE ${e.ref}`, e.entity, e.client].filter(Boolean).join(" · "));
    view.append(facts);
    if (!e.mailbox.ready && !template) view.append(el("p", "esign-alert", `The ${e.entity_short} mailbox is not connected yet. You can prepare this envelope, but sending needs a Super Admin to connect it under Company mailboxes.`));
    const grid = el("div", "esign-editor");
    const side = el("aside", "esign-side");
    side.append(recipientsPanel(), palettePanel(), propertiesPanel(), documentsPanel(), messagePanel(), settingsPanel());
    const pages = el("div", "esign-pages"); pages.id = "esign-pages";
    grid.append(side, pages);
    view.append(grid);
    renderPages();
  }

  function panel(title, open = true) { const d = el("details", "esign-panel"); d.open = open; d.append(el("summary", "", title)); return d; }

  function recipientsPanel() {
    const p = panel("Recipients");
    p.append(el("p", "esign-hint", "Recipients with the same order receive the envelope at the same time. Select a recipient, then place their fields."));
    editor.recipients.sort((a, b) => a.routing_order - b.routing_order).forEach(r => {
      const card = el("div", `esign-recipient${r.key === editor.active ? " active" : ""}`);
      card.style.setProperty("--who", color(r.key));
      const head = el("div", "esign-recipient-head");
      const pick = el("button", "esign-who", r.name || r.email || r.role_name || "New recipient"); pick.type = "button";
      pick.setAttribute("aria-pressed", String(r.key === editor.active));
      pick.addEventListener("click", () => { editor.active = r.key; renderEditor(); });
      const remove = button("×", () => {
        if (editor.fields.some(f => f.recipient === r.key) && !confirm("Remove this recipient and their fields?")) return;
        editor.recipients = editor.recipients.filter(x => x !== r); editor.fields = editor.fields.filter(f => f.recipient !== r.key);
        if (editor.active === r.key) editor.active = editor.recipients[0]?.key || null; dirty(); renderEditor();
      }, "icon-button"); remove.setAttribute("aria-label", "Remove recipient");
      head.append(pick, remove); card.append(head);
      const order = input(r.routing_order, "number", {min: 1, max: 20}); order.addEventListener("change", () => { r.routing_order = Number(order.value) || 1; dirty(); renderEditor(); });
      const role = select(Object.entries(ROLES), r.role);
      role.addEventListener("change", () => {
        if (role.value !== "signer" && editor.fields.some(f => f.recipient === r.key) && !confirm("Only signers have fields. Remove this recipient's fields?")) { role.value = r.role; return; }
        r.role = role.value; if (r.role !== "signer") editor.fields = editor.fields.filter(f => f.recipient !== r.key); dirty(); renderEditor();
      });
      const row1 = el("div", "esign-two"); row1.append(field("Order", order), field("Action", role)); card.append(row1);
      const name = input(r.name, "text", {maxLength: 150, placeholder: "Full name"}); name.addEventListener("input", () => { r.name = name.value; pick.textContent = name.value || "New recipient"; dirty(); });
      const mail = input(r.email, "email", {maxLength: 200, placeholder: "name@example.com"}); mail.addEventListener("input", () => { r.email = mail.value.trim(); dirty(); });
      card.append(field("Name", name), field("Email", mail));
      const more = el("details", "esign-more"); more.append(el("summary", "", "Access code and private message"));
      const code = input("", "text", {maxLength: 50, placeholder: r.has_access_code ? "Set: type to replace" : "Optional", autocomplete: "off"});
      code.addEventListener("input", () => { r.access_code = code.value; dirty(); });
      const note = Object.assign(el("textarea"), {value: r.note || "", maxLength: 1000, rows: 2}); note.addEventListener("input", () => { r.note = note.value; dirty(); });
      more.append(field("Access code (tell them separately)", code));
      if (r.has_access_code) { const l = el("label", "check-inline"); const c = input("", "checkbox"); c.addEventListener("change", () => { r.access_code = c.checked ? "" : null; dirty(); }); l.append(c, document.createTextNode("Remove access code")); more.append(l); }
      more.append(field("Private message", note));
      card.append(more);
      p.append(card);
    });
    p.append(button("+ Add recipient", () => {
      const key = `n${++keySeq}`;
      const order = Math.max(0, ...editor.recipients.map(r => r.routing_order)) + 1;
      editor.recipients.push({key, role: "signer", role_name: `Recipient ${editor.recipients.length + 1}`, routing_order: Math.min(order, 20), name: "", email: "", note: "", access_code: null});
      editor.active = key; dirty(); renderEditor();
    }));
    return p;
  }

  function palettePanel() {
    const p = panel("Fields");
    const active = editor.recipients.find(r => r.key === editor.active);
    if (!active || active.role !== "signer") { p.append(el("p", "esign-hint", "Select a recipient who needs to sign to place fields.")); return p; }
    p.append(el("p", "esign-hint", `Placing fields for ${active.name || "this recipient"}. Choose a field, then click on the page.`));
    const grid = el("div", "esign-palette");
    Object.entries(KINDS).forEach(([kind, [label]]) => {
      const b = button(label, () => { editor.placing = editor.placing === kind ? null : kind; renderEditor(); }, `esign-tool${editor.placing === kind ? " on" : ""}`);
      b.style.setProperty("--who", color(active.key)); grid.append(b);
    });
    p.append(grid);
    p.append(button("Initials on every page", () => {
      let added = 0;
      editor.e.documents.forEach(d => d.initials_spot.forEach((spot, index) => {
        if (editor.fields.some(f => f.document_id === d.id && f.page === index + 1 && f.kind === "initials" && f.recipient === active.key)) return;
        editor.fields.push({key: `f${++keySeq}`, recipient: active.key, document_id: d.id, page: index + 1, kind: "initials", ...spot, required: true, label: "", options: {}}); added++;
      }));
      dirty(); renderEditor(); ok(added ? `Added initials to ${added} page${added === 1 ? "" : "s"}. Drag any of them to adjust.` : "Every page already has initials for this recipient.");
    }));
    if (editor.placing) p.append(el("p", "esign-placing", `Click on a page to place: ${KINDS[editor.placing][0]}. Press Esc to stop.`));
    return p;
  }

  function propertiesPanel() {
    const p = panel("Selected field");
    const f = editor.fields.find(x => x.key === editor.selected);
    if (!f) { p.append(el("p", "esign-hint", "Select a field on a page to change it. Drag to move; drag the corner to resize; Delete removes it.")); return p; }
    p.append(el("strong", "", KINDS[f.kind][0]));
    const signers = editor.recipients.filter(r => r.role === "signer");
    const who = select(signers.map(r => [r.key, r.name || r.email || "Recipient"]), f.recipient);
    who.addEventListener("change", () => { f.recipient = who.value; dirty(); renderEditor(); });
    p.append(field("Recipient", who));
    if (!AUTO.includes(f.kind)) {
      const required = input("", "checkbox", {checked: f.required});
      required.addEventListener("change", () => { f.required = required.checked; dirty(); });
      const l = el("label", "check-inline"); l.append(required, document.createTextNode("Required")); p.append(l);
    } else p.append(el("p", "esign-hint", "Filled in automatically when the recipient signs."));
    if (["text", "company", "title", "dropdown", "checkbox"].includes(f.kind)) {
      const label = input(f.label, "text", {maxLength: 100, placeholder: KINDS[f.kind][0]}); label.addEventListener("input", () => { f.label = label.value; dirty(); paintField(f); });
      p.append(field("Label shown to the recipient", label));
    }
    if (f.kind === "dropdown") {
      const choices = Object.assign(el("textarea"), {rows: 4, value: (f.options?.choices || []).join("\n")});
      choices.addEventListener("input", () => { f.options = {choices: choices.value.split("\n").map(x => x.trim()).filter(Boolean)}; dirty(); });
      p.append(field("Choices (one per line)", choices));
    }
    p.append(button("Delete field", () => { editor.fields = editor.fields.filter(x => x !== f); editor.selected = null; dirty(); renderEditor(); }, "button button-quiet esign-danger"));
    return p;
  }

  function documentsPanel() {
    const p = panel("Documents");
    editor.e.documents.forEach(d => {
      const line = el("div", "esign-doc-line");
      line.append(el("span", "", `${d.name} · ${d.pages} p.`));
      const remove = button("×", async () => {
        if (!confirm(`Remove ${d.name} and its fields?`)) return;
        await save(true); const e = await api(`/api/esign/envelopes/${editor.e.id}/documents/${d.id}/remove`, {}); await openEditor(e.id, e);
      }, "icon-button"); remove.setAttribute("aria-label", `Remove ${d.name}`);
      line.append(remove); p.append(line);
    });
    const file = input("", "file", {accept: "application/pdf", multiple: true});
    file.addEventListener("change", async () => {
      try {
        await save(true);
        let e;
        for (const f of file.files) {
          if (f.size > 15000000) throw new Error(`${f.name} is larger than 15 MB.`);
          const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.onerror = reject; r.readAsDataURL(f); });
          e = await api(`/api/esign/envelopes/${editor.e.id}/documents`, {name: f.name, data});
        }
        if (e) await openEditor(e.id, e);
      } catch (error) { fail(error); }
    });
    p.append(field("Add PDF documents", file));
    return p;
  }

  function messagePanel() {
    const p = panel("Message", false);
    const message = Object.assign(el("textarea"), {value: editor.e.message, maxLength: 4000, rows: 6});
    message.addEventListener("input", () => { editor.e.message = message.value; dirty(); });
    p.append(field("Message to all recipients", message));
    return p;
  }

  function settingsPanel() {
    const p = panel("Reminders and expiration", false);
    const s = editor.e.settings;
    const number = (key, label, min, max) => { const i = input(s[key], "number", {min, max}); i.addEventListener("change", () => { s[key] = Number(i.value); dirty(); }); return field(label, i); };
    p.append(number("reminder_delay", "First reminder after (days)", 1, 30), number("reminder_every", "Then remind every (days, 0 = never)", 0, 30),
      number("expire_days", "Expires after (days)", 1, 365), number("warn_days", "Warn this many days before it expires", 0, 30));
    const attach = input("", "checkbox", {checked: s.attach_completed !== false});
    attach.addEventListener("change", () => { s.attach_completed = attach.checked; dirty(); });
    const l = el("label", "check-inline"); l.append(attach, document.createTextNode("Attach the completed documents to the final email")); p.append(l);
    return p;
  }

  function renderPages() {
    const container = $("esign-pages"); container.replaceChildren();
    if (!editor.e.documents.length) { container.append(el("p", "empty-state", "Add a PDF document to start.")); return; }
    editor.e.documents.forEach(d => {
      container.append(el("h4", "esign-doc-title", d.name));
      d.page_sizes.forEach((size, index) => {
        const number = index + 1;
        const page = el("div", "esign-page"); page.style.aspectRatio = `${size[0]} / ${size[1]}`;
        page.dataset.doc = d.id; page.dataset.page = number; page.dataset.w = size[0]; page.dataset.h = size[1];
        page.classList.toggle("placing", !!editor.placing);
        const cacheKey = `${d.id}:${number}`;
        let image = images.get(cacheKey);
        if (!image) {
          image = el("img"); image.loading = number <= 2 ? "eager" : "lazy"; image.alt = `${d.name}, page ${number}`; image.draggable = false;
          image.src = `/api/esign/documents/${d.id}/page/${number}.png`; images.set(cacheKey, image);
        }
        page.append(image, el("span", "esign-page-number", `${number} / ${d.pages}`));
        page.addEventListener("click", event => {
          if (!editor.placing || event.target.closest(".esign-field")) return;
          const box = page.getBoundingClientRect();
          const scale = size[0] / box.width;
          const [, w, h] = KINDS[editor.placing];
          const x = Math.min(Math.max(0, (event.clientX - box.left) * scale - w / 2), size[0] - w);
          const y = Math.min(Math.max(0, (event.clientY - box.top) * scale - h / 2), size[1] - h);
          const f = {key: `f${++keySeq}`, recipient: editor.active, document_id: d.id, page: number, kind: editor.placing, x: Math.round(x), y: Math.round(y), w, h,
            required: editor.placing !== "checkbox", label: "", options: editor.placing === "dropdown" ? {choices: ["Yes", "No"]} : {}};
          editor.fields.push(f); editor.selected = f.key; dirty(); renderEditor();
        });
        editor.fields.filter(f => f.document_id === d.id && f.page === number).forEach(f => page.append(fieldNode(f, size)));
        container.append(page);
      });
    });
    const selected = container.querySelector(".esign-field.selected");
    if (selected && editor.scrollTo) { selected.scrollIntoView({block: "center"}); editor.scrollTo = false; }
  }

  function fieldNode(f, size) {
    const node = el("div", `esign-field${f.key === editor.selected ? " selected" : ""}`);
    node.dataset.key = f.key; node.tabIndex = 0;
    node.style.setProperty("--who", color(f.recipient));
    const place = () => { node.style.left = `${f.x / size[0] * 100}%`; node.style.top = `${f.y / size[1] * 100}%`; node.style.width = `${f.w / size[0] * 100}%`; node.style.height = `${f.h / size[1] * 100}%`; };
    place();
    node.append(el("span", "esign-field-label", f.label || KINDS[f.kind][0]));
    const handle = el("span", "esign-handle"); handle.setAttribute("aria-hidden", "true"); node.append(handle);
    node.setAttribute("aria-label", `${KINDS[f.kind][0]} for ${editor.recipients.find(r => r.key === f.recipient)?.name || "recipient"}`);
    node.addEventListener("pointerdown", event => {
      event.preventDefault(); event.stopPropagation();
      if (editor.selected !== f.key) { editor.selected = f.key; document.querySelectorAll(".esign-field.selected").forEach(n => n.classList.remove("selected")); node.classList.add("selected"); refreshProperties(); }
      const page = node.parentElement, box = page.getBoundingClientRect(), scale = size[0] / box.width;
      const resize = event.target === handle, start = [event.clientX, event.clientY, f.x, f.y, f.w, f.h];
      node.setPointerCapture(event.pointerId);
      const move = ev => {
        const dx = (ev.clientX - start[0]) * scale, dy = (ev.clientY - start[1]) * scale;
        if (resize) { f.w = Math.max(8, Math.min(size[0] - f.x, start[4] + dx)); f.h = Math.max(8, Math.min(size[1] - f.y, start[5] + dy)); }
        else { f.x = Math.max(0, Math.min(size[0] - f.w, start[2] + dx)); f.y = Math.max(0, Math.min(size[1] - f.h, start[3] + dy)); }
        f.x = Math.round(f.x * 10) / 10; f.y = Math.round(f.y * 10) / 10; f.w = Math.round(f.w * 10) / 10; f.h = Math.round(f.h * 10) / 10;
        place(); dirty();
      };
      const up = () => { node.removeEventListener("pointermove", move); node.removeEventListener("pointerup", up); node.removeEventListener("pointercancel", up); };
      node.addEventListener("pointermove", move); node.addEventListener("pointerup", up); node.addEventListener("pointercancel", up);
    });
    node.addEventListener("click", event => event.stopPropagation());
    return node;
  }
  function paintField(f) { const node = document.querySelector(`.esign-field[data-key="${f.key}"] .esign-field-label`); if (node) node.textContent = f.label || KINDS[f.kind][0]; }
  function refreshProperties() {
    const side = document.querySelector(".esign-side"); if (!side) return;
    const old = [...side.children].find(c => c.querySelector("summary")?.textContent === "Selected field");
    if (old) old.replaceWith(propertiesPanel());
  }

  document.addEventListener("keydown", event => {
    if (!editor || document.body.dataset.view !== "esign" || event.target.closest("input,textarea,select,[contenteditable]")) return;
    if (event.key === "Escape" && editor.placing) { editor.placing = null; renderEditor(); return; }
    const f = editor.fields.find(x => x.key === editor.selected); if (!f) return;
    if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); editor.fields = editor.fields.filter(x => x !== f); editor.selected = null; dirty(); renderEditor(); return; }
    const step = event.shiftKey ? 10 : 1, moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[event.key];
    if (moves) {
      event.preventDefault();
      const doc = editor.e.documents.find(d => d.id === f.document_id), [w, h] = doc.page_sizes[f.page - 1];
      f.x = Math.max(0, Math.min(w - f.w, f.x + moves[0])); f.y = Math.max(0, Math.min(h - f.h, f.y + moves[1])); dirty(); renderPages();
    }
  });

  async function save(quiet) {
    const e = editor.e;
    const body = {version: e.version, message: e.message, settings: e.settings,
      recipients: editor.recipients.map(r => ({id: e.recipients.some(x => x.id === r.key) ? r.key : undefined, key: r.key, role: r.role, role_name: r.role_name, routing_order: r.routing_order,
        name: r.name, email: r.email, note: r.note || "", access_code: r.access_code})),
      fields: editor.fields.map(f => ({recipient: f.recipient, document_id: f.document_id, page: f.page, kind: f.kind, x: f.x, y: f.y, w: f.w, h: f.h, required: f.required, label: f.label, options: f.options}))};
    if (e.status === "template") body.template_name = e.template_name; else body.subject = e.subject;
    const saved = await api(`/api/esign/envelopes/${e.id}/draft`, body);
    await openEditor(saved.id, saved);
    if (!quiet) editor.dirty = false;
    return saved;
  }

  async function send() {
    const problems = [];
    editor.recipients.forEach(r => { if (!r.name || !r.email) problems.push(`Enter the name and email of every recipient.`); if (r.role === "signer" && !editor.fields.some(f => f.recipient === r.key && f.kind === "signature")) problems.push(`Add a signature field for ${r.name || "each signer"}.`); });
    if (problems.length) { fail(new Error([...new Set(problems)].join(" "))); return; }
    await save(true);
    const result = await api(`/api/esign/envelopes/${editor.e.id}/send`, {});
    editor = null;
    ok("Sent. Each recipient receives an email from the company mailbox when it is their turn.");
    await showEnvelope(result.envelope.id);
  }

  // ------------------------------------------------------------------ company mailboxes
  async function showMailboxes() {
    context("Company mailboxes");
    const d = await api("/api/esign/mailboxes");
    const view = $("mailbox-view"); view.replaceChildren();
    const head = el("div", "history-toolbar"); const title = el("div"); title.append(el("p", "eyebrow", "EMAIL"), el("h2", "", "Company mailboxes")); head.append(title);
    view.append(head, el("p", "portal-note", "Each company sends signing requests, reminders and completed documents from its own compliance mailbox. Replies and bounce notices in that inbox are attached to the envelope automatically."));
    d.mailboxes.forEach(box => view.append(d.can_manage ? mailboxForm(box) : mailboxStatus(box)));
  }
  function state(box) { return box.mode === "off" ? ["Not connected", "draft"] : box.smtp_error || box.imap_error ? ["Connected, with a problem", "declined"] : box.ready ? ["Connected", "completed"] : ["Incomplete", "sent"]; }
  function mailboxStatus(box) {
    const card = el("section", "panel esign-mailbox"); const body = el("div", "panel-body");
    const [label, tone] = state(box);
    body.append(el("h3", "", box.entity), chip(tone, label), el("p", "", box.address || "No address set"));
    if (box.smtp_error) body.append(el("p", "esign-alert", `Sending: ${box.smtp_error}`));
    if (box.imap_error) body.append(el("p", "esign-alert", `Inbox: ${box.imap_error}`));
    card.append(body); return card;
  }
  function mailboxForm(box) {
    const card = el("section", "panel esign-mailbox"); const body = el("div", "panel-body");
    const [label, tone] = state(box);
    const head = el("div", "esign-mailbox-head"); head.append(el("h3", "", box.entity), chip(tone, label)); body.append(head);
    if (!box.secrets_available) body.append(el("p", "esign-alert", "The server has no TFSC_SECRET_KEY, so passwords cannot be stored. Run sudo ./tfsc.sh update on the server."));
    const form = el("form", "esign-mailbox-form");
    const modes = [["mailbox", "Send from this company's own mailbox (recommended)"], ["relay", "Send through the platform's mail server; replies go to the mailbox"], ["off", "Not connected"]];
    const mode = select(modes, box.mode); mode.name = "mode";
    const address = input(box.address, "email", {name: "address", maxLength: 200}), display = input(box.display_name, "text", {name: "display_name", maxLength: 150});
    form.append(field("How this company sends email", mode));
    const two = el("div", "esign-two"); two.append(field("Mailbox address", address), field("Sender name shown to clients", display)); form.append(two);
    const own = el("div", "esign-own");
    const auth = select([["password", "App password"], ["google_sa", "Google Workspace service account"]], box.auth); auth.name = "auth";
    own.append(field("Sign-in method", auth));
    const secret = input("", "password", {name: "secret", maxLength: 500, autocomplete: "new-password", placeholder: box.has_secret ? "Saved. Enter a new one to replace it." : "16-character app password"});
    const passwordHelp = el("p", "esign-hint", "Google Workspace: sign in as this mailbox, turn on 2-Step Verification, then create an app password at myaccount.google.com/apppasswords and paste it here.");
    const sa = input("", "file", {name: "sa_key", accept: "application/json"});
    const saHelp = el("p", "esign-hint", box.has_sa_key ? `Key saved for ${box.sa_client_email}. Upload a new JSON key to replace it.` :
      "In the Google Admin console (Security > API controls > Domain-wide delegation), allow the service account's client ID the scope https://mail.google.com/, then upload its JSON key.");
    const passwordBlock = el("div"); passwordBlock.append(field("App password", secret), passwordHelp);
    const saBlock = el("div"); saBlock.append(field("Service account key (JSON)", sa), saHelp);
    own.append(passwordBlock, saBlock);
    const advanced = el("details", "esign-more"); advanced.append(el("summary", "", "Mail servers (Google Workspace is filled in)"));
    const login = input(box.login, "text", {name: "login", maxLength: 200});
    const smtp = el("div", "esign-three"); smtp.append(field("SMTP server", input(box.smtp_host, "text", {name: "smtp_host"})), field("Port", input(box.smtp_port, "number", {name: "smtp_port"})),
      field("Security", select([["starttls", "STARTTLS"], ["ssl", "SSL/TLS"], ["none", "None (test server only)"]], box.smtp_security)));
    smtp.lastChild.querySelector("select").name = "smtp_security";
    const imap = el("div", "esign-three"); imap.append(field("IMAP server", input(box.imap_host, "text", {name: "imap_host"})), field("Port", input(box.imap_port, "number", {name: "imap_port"})),
      field("Security", select([["ssl", "SSL/TLS"], ["starttls", "STARTTLS"], ["none", "None (test server only)"]], box.imap_security)));
    imap.lastChild.querySelector("select").name = "imap_security";
    advanced.append(field("Sign-in name", login), smtp, imap);
    const inbox = input("", "checkbox", {name: "imap_enabled", checked: box.imap_enabled}); const inboxLabel = el("label", "check-inline"); inboxLabel.append(inbox, document.createTextNode("Read replies and bounce notices from this inbox (read-only; nothing is changed in the mailbox)"));
    own.append(inboxLabel, advanced);
    form.append(own);
    form.append(field("Company signer's email (used when the company signs electronically)", input(box.signer_email, "email", {name: "signer_email", maxLength: 200})));
    const notify = el("fieldset", "esign-notify"); notify.append(el("legend", "", "Email the sender and this mailbox when a recipient…"));
    [["viewed", "views the documents"], ["signed", "signs"], ["completed", "everyone has finished (with the documents attached)"], ["declined", "declines"], ["bounced", "an email bounces"],
      ["replied", "replies by email (to the sender)"], ["auth_failed", "is locked out by wrong access codes"], ["voided", "the envelope is voided"], ["expired", "the envelope expires"]].forEach(([key, text]) => {
      const c = input(key, "checkbox", {checked: box.notify.includes(key), name: "notify"}); const l = el("label", "check-inline"); l.append(c, document.createTextNode(text)); notify.append(l);
    });
    form.append(notify);
    const sync = () => { own.hidden = mode.value !== "mailbox"; passwordBlock.hidden = auth.value !== "password"; saBlock.hidden = auth.value !== "google_sa"; };
    mode.addEventListener("change", sync); auth.addEventListener("change", sync); sync();
    const result = el("p", "esign-test-result"); result.setAttribute("role", "status");
    const actions = el("div", "esign-actions");
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
    const to = input(user.email, "email", {placeholder: "Send a test to"}); to.setAttribute("aria-label", "Send a test email to");
    actions.append(to, button("Send test email", async () => { result.textContent = "Sending…"; const r = await api(`/api/esign/mailboxes/${box.index}/test`, {what: "send", to: to.value}); result.textContent = r.message; result.className = `esign-test-result ${r.ok ? "good" : "bad"}`; }));
    actions.append(button("Check inbox", async () => { result.textContent = "Signing in to the inbox…"; const r = await api(`/api/esign/mailboxes/${box.index}/test`, {what: "inbox"}); result.textContent = r.message; result.className = `esign-test-result ${r.ok ? "good" : "bad"}`; }));
    const status = el("div", "esign-mailbox-status");
    if (box.smtp_error) status.append(el("p", "esign-alert", `Last sending problem: ${box.smtp_error}`));
    if (box.imap_error) status.append(el("p", "esign-alert", `Last inbox problem: ${box.imap_error}`));
    if (box.imap_last_poll && !box.imap_error && box.mode === "mailbox" && box.imap_enabled) status.append(el("p", "esign-hint", `Inbox last checked ${when(box.imap_last_poll)}.`));
    Object.values(box.last_test || {}).forEach(t => status.append(el("p", `esign-hint ${t.ok ? "" : "esign-attention"}`, `${t.what === "inbox" ? "Inbox check" : "Test email"} ${when(t.at)}: ${t.message}`)));
    body.append(form, actions, result, status); card.append(body);
    return card;
  }

  // ------------------------------------------------------------------ start-up
  window.addEventListener("beforeunload", event => { if (editor?.dirty) { event.preventDefault(); event.returnValue = ""; } });
  window.esignReady = config => {
    user = config.user; entities = config.entities || [];
    $("show-esign").onclick = () => window.esignShowList();
    $("show-mailboxes").onclick = () => window.esignShowMailboxes();
    $("show-mailboxes").hidden = false;
    api("/api/esign/envelopes?box=action").then(d => badge(d.counts.action)).catch(() => {});
    const params = new URLSearchParams(location.search);
    const id = params.get("envelope");
    if (id && /^[a-f0-9]{32}$/.test(id)) window.esignOpen(id);
    else if (params.get("view") === "signatures") window.esignShowList();
  };
  window.esignLeave = guard;
})();
