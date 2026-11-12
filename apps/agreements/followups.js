/* Follow-up notes: post what you did to move an agreement forward, and see what everyone else did, with date and time.
   window.tfscFollowups.open({envelope_id | contract_id | client_id, title, onSaved})   the dialog (history + new note)
   window.tfscFollowups.panel({..same..})                                             the same, as a card inside a page */
"use strict";
(() => {
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; };
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { window.tfscSignIn?.("expired"); throw new Error("Please sign in again."); }
    if (!r.ok) throw new Error(d.error || "The request could not be completed.");
    return d;
  }
  const when = ts => new Date(ts * 1000).toLocaleString([], {month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit"});
  const day = iso => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString([], {month: "short", day: "numeric", year: "numeric"}); };
  const query = t => t.envelope_id ? `envelope_id=${encodeURIComponent(t.envelope_id)}` : t.contract_id ? `contract_id=${encodeURIComponent(t.contract_id)}` : `client_id=${encodeURIComponent(t.client_id)}`;
  const ICON = {phone: "☎", email: "✉", meeting: "👥", message: "💬", other: "•"};

  function list(notes) {
    const ol = el("ol", "fu-list");
    if (!notes.length) { const li = el("li", "fu-empty", "No follow-up notes yet. The first note you post appears here for everyone who works on this client."); ol.append(li); return ol; }
    notes.forEach(n => {
      const li = el("li", "fu-item"); const icon = el("span", "fu-icon", ICON[n.channel] || "•"); icon.setAttribute("aria-hidden", "true");
      const main = el("div", "fu-main"); const meta = el("div", "fu-meta");
      meta.append(el("strong", "", n.author), el("span", "", `${n.channel_label} · ${when(n.created)}`));
      main.append(meta, el("p", "fu-body", n.body));
      if (n.next_on) main.append(el("p", "fu-next", `Next follow-up: ${day(n.next_on)}`));
      li.append(icon, main); ol.append(li);
    });
    return ol;
  }
  function form(target, channels, after) {
    const f = el("form", "fu-form");
    const grid = el("div", "fu-grid");
    const ch = el("select"); ch.id = "fu-channel-" + Math.random().toString(36).slice(2, 7);
    Object.entries(channels).forEach(([k, v]) => { const o = el("option", "", v); o.value = k; ch.append(o); });
    const next = el("input"); next.type = "date"; next.id = ch.id + "-next";
    const l1 = el("label", "", "How"); l1.htmlFor = ch.id; const l2 = el("label", "", "Next follow-up (optional)"); l2.htmlFor = next.id;
    const a = el("div", "field"); a.append(l1, ch); const b = el("div", "field"); b.append(l2, next); grid.append(a, b);
    const body = el("textarea"); body.rows = 3; body.maxLength = 4000; body.required = true; body.id = ch.id + "-body";
    body.placeholder = "What happened? For example: Called Dr. Opara's office, spoke with the practice manager; she will sign by Friday.";
    const l3 = el("label", "", "Note"); l3.htmlFor = body.id; const c = el("div", "field"); c.append(l3, body);
    const err = el("p", "fu-error"); err.hidden = true; err.setAttribute("role", "alert");
    const row = el("div", "fu-actions"); const save = el("button", "button button-primary", "Post note"); save.type = "submit"; row.append(save);
    f.append(grid, c, err, row);
    f.addEventListener("submit", async e => {
      e.preventDefault(); err.hidden = true; save.disabled = true;
      try {
        await api("/api/followups", {envelope_id: target.envelope_id, contract_id: target.contract_id, client_id: target.client_id, channel: ch.value, body: body.value, next_on: next.value || null});
        body.value = ""; next.value = ""; await after();
      } catch (x) { err.textContent = x.message; err.hidden = false; } finally { save.disabled = false; }
    });
    return f;
  }
  async function fill(box, target) {
    const d = await api("/api/followups?" + query(target));
    const history = el("div", "fu-history"); history.append(list(d.notes));
    const refresh = async () => { await fill(box, target); target.onSaved?.(); };
    box.replaceChildren(form(target, d.channels, refresh), el("h3", "fu-title", `Follow-up history (${d.notes.length})`), history);
  }
  function open(target) {
    const dialog = el("dialog", "portal-dialog fu-dialog"); dialog.setAttribute("aria-labelledby", "fu-dialog-title");
    const head = el("div", "dialog-heading"); const t = el("div"); t.append(el("p", "eyebrow", "Follow-up notes"), el("h2", "", target.title || "Follow up")); t.lastChild.id = "fu-dialog-title";
    const x = el("button", "icon-button close-dialog", "×"); x.type = "button"; x.setAttribute("aria-label", "Close follow-up notes"); x.addEventListener("click", () => dialog.close());
    head.append(t, x); const body = el("div", "dialog-body"); body.append(el("p", "muted small", "Loading…"));
    dialog.append(head, body); document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
    fill(body, target).catch(e => { body.replaceChildren(el("p", "fu-error", e.message)); });
  }
  function panel(target) {
    const card = el("section", "card fu-card"); const h = el("h2", "", "Follow-up notes");
    card.append(h, el("p", "muted small", "Post what you did to move this forward (a call, an email, a meeting). Everyone who works on this client sees the notes, with the date and time."));
    const body = el("div"); body.append(el("p", "muted small", "Loading…")); card.append(body);
    fill(body, target).catch(e => { body.replaceChildren(el("p", "fu-error", e.message)); });
    return card;
  }
  window.tfscFollowups = {open, panel, when};
})();
