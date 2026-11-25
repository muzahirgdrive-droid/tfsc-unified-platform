/* Reminders (release 5.4): Admin › Reminders (rules, company defaults, do-not-remind list, safety guards), the Reminder centre
   (#/reminders: upcoming, sent, paused and off, needs a look) and the Reminders card on an envelope. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; };
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {cache: "no-store"} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { window.tfscSignIn?.("expired"); throw new Error("Please sign in again."); }
    if (!r.ok) throw new Error(d.error || "The request could not be completed.");
    return d;
  }
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  // tz: show the time in the company's time zone (reminders go out in the company's sending hours), else the viewer's own
  const when = (ts, tz) => { if (!ts) return ""; const o = {weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}; try { if (tz) o.timeZone = tz; return new Date(ts * 1000).toLocaleString([], o); } catch { delete o.timeZone; return new Date(ts * 1000).toLocaleString([], o); } };
  const day = ts => ts ? new Date(ts * 1000).toLocaleDateString([], {dateStyle: "medium"}) : "";
  const FOREVER = 4102444800;
  function fail(e) { const box = $("app-error"); if (box) { box.textContent = e.message; box.hidden = false; box.scrollIntoView?.({block: "nearest"}); } }
  function toast(t) { if (window.tfscToast) return window.tfscToast(t); let x = document.querySelector(".toast"); if (!x) { x = el("div", "toast"); x.setAttribute("role", "status"); document.body.append(x); } x.textContent = t; x.hidden = false; setTimeout(() => { x.hidden = true; }, 4000); }
  function button(text, cls, fn) { const b = el("button", cls || "button button-quiet button-sm", text); b.type = "button"; b.addEventListener("click", ev => { ev.stopPropagation(); if (b.dataset.busy) return; b.dataset.busy = "1"; b.setAttribute("aria-busy", "true"); Promise.resolve().then(() => fn(ev)).catch(fail).finally(() => { delete b.dataset.busy; b.removeAttribute("aria-busy"); }); }); return b; }
  function chip(text, tone) { return el("span", `rem-chip ${tone || ""}`, text); }
  function head(eyebrow, title, sub, ...actions) {
    const h = el("div", "page-head"); const t = el("div"); t.append(el("p", "eyebrow", eyebrow), el("h1", "", title), el("p", "page-sub", sub));
    const a = el("div", "page-actions"); actions.filter(Boolean).forEach(x => a.append(x)); h.append(t, a); return h;
  }
  function card(title, sub) {
    const c = el("section", "cat-card"); const h = el("header"); const t = el("div"); t.append(el("h2", "", title)); if (sub) t.append(el("p", "", sub));
    h.append(t); c.append(h); const body = el("div", "cat-body"); c.append(body); return [c, body, h];
  }
  let seq = 0;
  function field(label, control, note) {
    const f = el("div", "rem-f"); const l = el("label", "", label); control.id = control.id || `rem-${++seq}`; l.htmlFor = control.id;
    f.append(l, control); if (note) f.append(el("p", "rem-note", note)); return f;
  }
  function number(value, min, max) { const i = el("input"); i.type = "number"; i.min = min; i.max = max; i.value = value; i.className = "rem-input"; return i; }
  function select(options, value) { const s = el("select"); s.className = "rem-input"; options.forEach(([v, t]) => s.append(new Option(t, v))); s.value = value; return s; }
  function toggle(title, sub, on) {
    const l = el("label", "rem-switch"); const i = el("input"); i.type = "checkbox"; i.checked = !!on; i.setAttribute("role", "switch");
    const t = el("span", "t"); t.append(el("strong", "", title), el("span", "", sub)); l.append(i, t); return [l, i];
  }
  // a small dialog asking for a date (pause until); resolves to epoch seconds, "forever", or null when cancelled
  function askDate(title, text, allowForever) {
    return new Promise(resolve => {
      const d = el("dialog", "portal-dialog rem-dialog"); const hd = el("div", "dialog-heading"); hd.append(el("h2", "", title));
      const body = el("div", "dialog-body"); body.append(el("p", "muted small", text));
      const input = el("input"); input.type = "date"; input.className = "rem-input";
      const t = new Date(Date.now() + 7 * 86400e3); input.value = t.toISOString().slice(0, 10); input.min = new Date(Date.now() + 86400e3).toISOString().slice(0, 10);
      body.append(field("Pause until (morning of)", input));
      const row = el("div", "rem-dialog-actions");
      const done = v => { d.close(); d.remove(); resolve(v); };
      row.append(button("Cancel", "button button-quiet", () => done(null)));
      if (allowForever) row.append(button("Until I resume", "button button-quiet", () => done("forever")));
      row.append(button("Pause", "button button-primary", () => { const v = input.value ? new Date(input.value + "T08:00:00").getTime() / 1000 : null; done(v); }));
      body.append(row); d.append(hd, body); document.body.append(d); d.addEventListener("cancel", () => done(null)); d.showModal();
    });
  }

  // ================================================================== Admin › Reminders
  let rulesCache = null;
  async function rules(force) { if (!rulesCache || force) rulesCache = await api("/api/reminders/rules"); return rulesCache; }
  function adminTabs(active) {
    const nav = el("nav", "rem-tabs"); nav.setAttribute("aria-label", "Reminders");
    [["rules", "Rules", "#/admin/reminders"], ["optouts", "Do-not-remind list", "#/admin/reminders/optouts"], ["guards", "Safety guards", "#/admin/reminders/guards"]].forEach(([k, t, h]) => {
      const a = el("a", "", t); a.href = h; if (k === active) a.setAttribute("aria-current", "page"); nav.append(a);
    });
    return nav;
  }
  async function showAdmin(tab = "rules", editing) {
    window.portalMark?.(tab === "rules" ? "#/admin/reminders" : `#/admin/reminders/${tab}`);
    const v = $("admin-view"); v.replaceChildren();
    if (tab === "optouts") return showOptouts(v);
    const d = await rules(true);
    const pauseBtn = d.paused_until ? button("Resume all reminders", "button button-primary", async () => { await api("/api/reminders/pause", {until: null}); toast("Reminders resumed."); showAdmin(tab); })
      : button("Pause all reminders", "button button-danger", async () => {
        const until = await askDate("Pause every reminder", "No automatic reminder goes out to anyone until then. Invitations, signed copies and \"Remind now\" still work.", true);
        if (until === null) return; await api("/api/reminders/pause", {until}); toast("All reminders paused."); showAdmin(tab);
      });
    v.append(head("Admin", "Reminders", "Decide when, how often and how reminders go out, and who never receives them. Senders choose one of these rules when they send.", pauseBtn));
    if (d.paused_until) v.append(el("div", "notice notice-warning", d.paused_until >= FOREVER ? "All automatic reminders are paused until you resume them." : `All automatic reminders are paused until ${when(d.paused_until)}.`));
    v.append(adminTabs(tab));
    if (tab === "guards") return v.append(guards(d));
    const grid = el("div", "rem-grid"); const left = el("div"); const right = el("div");
    const current = d.rules.find(r => r.id === editing) || d.rules.find(r => r.id === "standard") || d.rules[0];
    const ORDER = ["standard", "gentle", "firm", "off"];
    const rank = r => ORDER.includes(r.id) ? ORDER.indexOf(r.id) : ORDER.length;
    d.rules.filter(r => r.active || r.id === current?.id).sort((a, b) => rank(a) - rank(b)).forEach(r => {
      const a = el("a", `rem-policy${r.id === current?.id ? " on" : ""}`); a.href = "#"; a.addEventListener("click", ev => { ev.preventDefault(); showAdmin("rules", r.id).catch(fail); });
      const s = el("strong", "", r.name); if (!r.active) s.append(chip("Retired", "grey")); a.append(s, el("p", "", r.summary));
      const uses = el("div", "uses"); const df = r.default_for || [];
      if (df.length && df.length === d.companies.length) uses.append(chip(d.companies.length === 1 ? "Default" : "Default for every company"));
      else if (df.length > 2) uses.append(chip(`Default for ${df.length} companies`));
      else df.forEach(x => uses.append(chip(`Default: ${x}`))); uses.append(chip(`${r.envelopes} envelope${r.envelopes === 1 ? "" : "s"} out now`, "grey")); a.append(uses);
      left.append(a);
    });
    left.append(button("+ New rule", "button button-quiet rem-wide", () => editRule(right, null, d)));
    const [dc, db] = card("Default rule per company", "Used when the sender does not choose another rule.");
    const active = d.rules.filter(r => r.active);
    const picks = d.companies.map(co => { const s = select(active.map(r => [r.id, r.name]), co.rule); db.append(field(co.short, s, `Time zone: ${co.timezone.replace("_", " ")}`)); return [co.name, s]; });
    db.append(button("Save defaults", "button button-primary button-sm rem-save", async () => {
      await api("/api/reminders/defaults", {defaults: Object.fromEntries(picks.map(([n, s]) => [n, s.value]))}); toast("Defaults saved."); showAdmin("rules", current?.id);
    }));
    left.append(dc);
    grid.append(left, right); v.append(grid);
    editRule(right, current, d);
  }
  function editRule(box, r, d) {
    const isNew = !r; const off = r?.id === "off";
    r = r || {name: "", description: "", first_after: 2, every: 3, max_count: 4, weekdays: [0, 1, 2, 3, 4], hour_from: 9, hour_to: 17, combine: true, skip_opened_hours: 24,
      remind_viewers: false, allow_signer_stop: true, notify_sender_after_last: true, active: true, builtin: false};
    const [c, b] = card(isNew ? "New rule" : r.name, isNew ? "A schedule senders can choose when they send." : (r.builtin ? "A built-in rule: its numbers can be changed, it cannot be retired." : "Changes apply to envelopes already out from their next reminder."));
    const name = el("input"); name.className = "rem-input"; name.value = r.name; name.maxLength = 80;
    const first = number(r.first_after, 1, 30), every = number(r.every, 1, 30), most = number(r.max_count, 0, 20);
    const from = select([...Array(24).keys()].map(h => [String(h), `${h}:00`]), String(r.hour_from)), to = select([...Array(24).keys()].map(h => [String(h + 1), `${h + 1}:00`]), String(r.hour_to));
    const skip = number(r.skip_opened_hours, 0, 168);
    b.append(field("Name", name));
    if (off) b.append(el("p", "rem-note", "No automatic reminders are sent under this rule. The invitation, corrections and the signed copy are still sent, and the sender can use \"Remind now\"."));
    const timing = el("div", "rem-form");
    timing.append(field("First reminder after", first, "working days after the invitation"), field("Then every", every, "working days"), field("At most", most, "reminders per person (0 = none)"),
      field("From", from, "in the sending company's time zone"), field("Until", to), field("Skip if opened within", skip, "hours (0 = never skip)"));
    const days = el("div", "rem-days"); const boxes = DAYS.map((t, i) => { const l = el("label", "rem-day"); const x = el("input"); x.type = "checkbox"; x.checked = (r.weekdays || []).includes(i); l.append(x, document.createTextNode(t)); days.append(l); return x; });
    if (!off) { b.append(el("p", "rem-sect", "When"), timing, field("Send on", days)); }
    const [t1, combine] = toggle("One email per person per day", "Someone with several envelopes from the same company waiting gets one email that lists them all.", r.combine);
    const [t2, viewers] = toggle("Remind people who only view a copy", "Usually off: viewers never hold anything up.", r.remind_viewers);
    const [t3, stop] = toggle("Let signers stop reminders from the email", "A link at the bottom of each reminder. The sender is told at once; the person can still sign.", r.allow_signer_stop);
    const [t4, notify] = toggle("After the last reminder, tell the sender", "Instead of emailing the signer again: \"… has not signed after 4 reminders.\"", r.notify_sender_after_last);
    if (!off) { const sw = el("div", "rem-switches"); sw.append(t1, t2, t3, t4); b.append(el("p", "rem-sect", "How"), sw); }
    const err = el("p", "rem-error"); err.hidden = true; err.setAttribute("role", "alert"); b.append(err);
    const actions = el("div", "cat-actions"); actions.append(el("span", "sp"));
    if (!isNew && !r.builtin) actions.append(button(r.active ? "Retire rule" : "Bring back", "button button-quiet", async () => {
      await api(`/api/reminders/rules/${r.id}`, {active: !r.active}); rulesCache = null; showAdmin("rules", r.id);
    }));
    actions.append(button(isNew ? "Add rule" : "Save rule", "button button-primary", async () => {
      err.hidden = true;
      const body = {name: name.value, description: r.description || "", first_after: Number(first.value), every: Number(every.value), max_count: Number(most.value),
        hour_from: Number(from.value), hour_to: Number(to.value), skip_opened_hours: Number(skip.value), weekdays: boxes.map((x, i) => x.checked ? i : -1).filter(i => i >= 0),
        combine: combine.checked, remind_viewers: viewers.checked, allow_signer_stop: stop.checked, notify_sender_after_last: notify.checked};
      try {
        const res = await api(isNew ? "/api/reminders/rules" : `/api/reminders/rules/${r.id}`, body); rulesCache = null;
        toast("Rule saved."); showAdmin("rules", isNew ? res.id : r.id);
      } catch (x) { err.textContent = x.message; err.hidden = false; }
    }));
    c.append(actions);
    box.replaceChildren(c);
  }
  function guards(d) {
    const [c, b] = card("Safety guards", "Always on, for every rule and every email. They cannot be switched off.");
    [["Each email is sent at most once.", "If the mail server accepted it but never confirmed, it is not sent again by itself; it waits in the Reminder centre under \"Needs a look\"."],
     ["Every try is counted.", "An email the mail server refuses is retried a few times, then given up, and the sender is told."],
     [`At most ${d.guards.per_company_per_day} automatic email per person per company per day, and ${d.guards.per_person_per_day} in all.`, "More wait for the next sending day and go in one combined email."],
     ["No new reminder while an earlier one to the same person is still waiting to go out.", "So a mailbox that was down for a day never releases a pile of reminders at once."],
     ["Never to an address that bounced or failed,", "never after the person signed, declined or was replaced, never after the envelope is completed, voided or expired."],
     ["\"Remind now\" at most once a day per person and envelope,", "and never to someone on the do-not-remind list."]].forEach(([t, x]) => {
      const g = el("div", "rem-guard"); g.append(el("span", "ic", "✓")); const s = el("div"); s.append(el("strong", "", t + " "), document.createTextNode(x)); g.append(s); b.append(g);
    });
    return c;
  }
  async function showOptouts(v) {
    const d = await api("/api/reminders/optouts"); const r = await rules();
    v.append(head("Admin", "Reminders", "Decide when, how often and how reminders go out, and who never receives them.",
      d.can_edit && button("+ Add a person", "button button-primary", () => addOptout(r.companies, () => showAdmin("optouts")))));
    v.append(adminTabs("optouts"));
    const [c, b] = card("Do-not-remind list", "These people never receive automatic reminders or \"Remind now\". Invitations, corrections and signed copies are still sent.");
    if (!d.optouts.length) b.append(el("p", "rem-note", "Nobody is on the list."));
    else {
      const t = el("table", "rtable"); const hr = el("tr"); ["Person", "Applies to", "Reason", "Added", "Until", ""].forEach(x => hr.append(el("th", "", x)));
      const th = el("thead"); th.append(hr); const tb = el("tbody");
      d.optouts.forEach(o => {
        const tr = el("tr"); const who = el("td"); who.append(el("b", "", o.name || o.email)); if (o.name) who.append(el("small", "", o.email));
        const company = r.companies.find(x => x.name === o.entity);
        const added = el("td", "", o.created_by_name); added.append(el("small", "", day(o.created)));
        const act = el("td", "act"); if (d.can_edit) act.append(button("Remove", "button button-quiet button-sm", async () => {
          if (!confirm(`Remove ${o.name || o.email} from the do-not-remind list? Reminders start again from their next planned date.`)) return;
          await api(`/api/reminders/optouts/${o.id}/remove`, {}); showAdmin("optouts");
        }));
        tr.append(who, el("td", "", o.entity ? `Only ${company ? company.short : o.entity}` : "Every company"), el("td", "", o.reason), added, el("td", "", o.until ? day(o.until) : "Always"), act);
        tb.append(tr);
      });
      t.append(th, tb); const wrap = el("div", "rem-scroll"); wrap.append(t); b.append(wrap);
    }
    v.append(c);
  }
  function addOptout(companies, after, preset = {}) {
    const d = el("dialog", "portal-dialog rem-dialog"); const hd = el("div", "dialog-heading"); const t = el("div"); t.append(el("p", "eyebrow", "Do-not-remind list"), el("h2", "", "Stop reminders for a person"));
    const x = el("button", "icon-button close-dialog", "×"); x.type = "button"; x.setAttribute("aria-label", "Close"); x.addEventListener("click", () => d.close()); hd.append(t, x);
    const body = el("div", "dialog-body");
    const name = el("input"); name.className = "rem-input"; name.value = preset.name || ""; name.maxLength = 150;
    const email = el("input"); email.className = "rem-input"; email.type = "email"; email.value = preset.email || ""; email.maxLength = 200;
    const scope = select([["", "Every company"], ...companies.map(co => [co.name, `Only ${co.short}`])], preset.entity || "");
    const until = el("input"); until.type = "date"; until.className = "rem-input"; until.min = new Date(Date.now() + 86400e3).toISOString().slice(0, 10);
    const reason = el("input"); reason.className = "rem-input"; reason.maxLength = 500; reason.placeholder = "For example: asked by phone on Nov 25 to stop reminders";
    const err = el("p", "rem-error"); err.hidden = true; err.setAttribute("role", "alert");
    body.append(field("Name", name), field("Email", email), field("Applies to", scope), field("Until (leave empty for always)", until), field("Reason", reason, "Required. Shown to the team and kept in the Activity log."), err);
    const row = el("div", "rem-dialog-actions");
    const cancel = button("Cancel", "button button-quiet", () => d.close()); cancel.dataset.dialogClose = ""; row.append(cancel, button("Stop reminders", "button button-primary", async () => {
      err.hidden = true;
      try {
        await api("/api/reminders/optouts", {name: name.value, email: email.value, entity: scope.value || null, reason: reason.value, until: until.value ? new Date(until.value + "T23:59:00").getTime() / 1000 : null});
        d.close(); toast("Added to the do-not-remind list."); after?.();
      } catch (x2) { err.textContent = x2.message; err.hidden = false; }
    }));
    body.append(row); d.append(hd, body); document.body.append(d); d.addEventListener("close", () => d.remove()); d.showModal();
  }

  // ================================================================== Reminder centre
  const centreState = {tab: "upcoming", q: "", company: "", selected: new Set()};
  const STATE_TEXT = {paused: ["Paused", "amber"], off: ["Off", "red"], opted_out: ["Do-not-remind list", "red"], undeliverable: ["Email undeliverable", "red"], rule_off: ["Rule: Off", "grey"]};
  async function showCentre(tab) {
    if (tab) centreState.tab = tab;
    window.portalMark?.(centreState.tab === "upcoming" ? "#/reminders" : `#/reminders/${centreState.tab}`);
    const d = await api("/api/reminders/centre?days=7"); const r = await rules();
    const v = $("admin-view"); v.replaceChildren();
    const actions = [button("Agreements", "button button-quiet", () => window.portalRoute?.("#/agreements"))];
    if (d.is_super) actions.push(button("Reminder rules", "button button-quiet", () => window.portalRoute?.("#/admin/reminders")));
    v.append(head("Agreements", "Reminders", "Every reminder about to go out, everything sent, and everything paused. Senders see their own envelopes; Admins their team's.", ...actions));
    if (d.paused_until) v.append(el("div", "notice notice-warning", d.paused_until >= FOREVER ? "All automatic reminders are paused (Admin › Reminders)." : `All automatic reminders are paused until ${when(d.paused_until)}.`));
    const tiles = el("div", "rem-tiles");
    [["Next 7 days", d.counts.upcoming, `to ${d.counts.people} ${d.counts.people === 1 ? "person" : "people"}`, ""], ["Sent last 7 days", d.counts.sent_week, "reminders and warnings", ""],
     ["Paused or off", d.counts.held, `${d.counts.optouts} on the do-not-remind list`, ""], ["Needs a look", d.counts.attention, "delivery not confirmed", d.counts.attention ? "warn" : ""]].forEach(([a, b, c, cls]) => {
      const t = el("div", `rem-tile ${cls}`); t.append(el("span", "", a), el("strong", "", b), el("small", "", c)); tiles.append(t);
    });
    v.append(tiles);
    const nav = el("nav", "rem-tabs");
    [["upcoming", "Upcoming", d.counts.upcoming], ["sent", "Sent", d.sent.length], ["held", "Paused and off", d.counts.held], ["attention", "Needs a look", d.counts.attention]].forEach(([k, t, n]) => {
      const a = el("a", "", t); a.href = k === "upcoming" ? "#/reminders" : `#/reminders/${k}`; a.append(el("span", "n", n)); if (k === centreState.tab) a.setAttribute("aria-current", "page"); nav.append(a);
    });
    v.append(nav);
    const box = el("section", "cat-card"); v.append(box);
    const filters = el("div", "rem-filters");
    const q = el("input"); q.className = "rem-input"; q.type = "search"; q.placeholder = "Search person, email or envelope"; q.value = centreState.q; q.setAttribute("aria-label", "Search");
    const company = select([["", "All companies"], ...r.companies.map(co => [co.name, co.short])], centreState.company); company.setAttribute("aria-label", "Company");
    q.addEventListener("input", () => { centreState.q = q.value; paint(); }); company.addEventListener("change", () => { centreState.company = company.value; paint(); });
    filters.append(q); if (r.companies.length > 1) filters.append(company);
    const body = el("div"); box.append(filters, body);
    const match = x => (!centreState.company || x.entity === centreState.company) && (!centreState.q || [x.name, x.email, x.to, x.subject, x.envelope_subject, x.ref].join(" ").toLowerCase().includes(centreState.q.toLowerCase()));
    const open = id => button("Open", "button button-quiet button-sm", () => window.portalRoute?.(`#/envelope/${id}`));
    const after = async msg => { if (msg) toast(msg); centreState.selected.clear(); await showCentre(); };
    const act = async (rid, action, until) => { await api(`/api/reminders/recipients/${rid}`, {action, until}); };
    function paint() {
      body.replaceChildren();
      if (centreState.tab === "upcoming") {
        const rows = d.upcoming.filter(match);
        if (!rows.length) return body.append(el("p", "rem-empty", "No reminders are planned in the next 7 days."));
        const bulk = el("div", "rem-bulk"); const count = el("b", "", "");
        const refresh = () => { count.textContent = `${centreState.selected.size} selected`; bulk.hidden = !centreState.selected.size; };
        bulk.append(count, button("Skip the next reminder", "button button-quiet button-sm", async () => { const res = await api("/api/reminders/bulk", {recipient_ids: [...centreState.selected], action: "skip"}); after(`${res.done} skipped.`); }),
          button("Pause until…", "button button-quiet button-sm", async () => { const u = await askDate("Pause reminders", "For the selected people on these envelopes.", true); if (u === null) return; const res = await api("/api/reminders/bulk", {recipient_ids: [...centreState.selected], action: "pause", until: u}); after(`${res.done} paused.`); }),
          button("Switch off for these people", "button button-quiet button-sm", async () => { if (!confirm("Switch reminders off for the selected people on these envelopes?")) return; const res = await api("/api/reminders/bulk", {recipient_ids: [...centreState.selected], action: "off"}); after(`${res.done} switched off.`); }));
        body.append(bulk);
        const t = el("table", "rtable"); const hr = el("tr"); ["", "When", "To", "Envelope", "Which", "Rule", ""].forEach(x => hr.append(el("th", "", x)));
        const th = el("thead"); th.append(hr); const tb = el("tbody");
        rows.forEach(x => {
          const tr = el("tr"); const cb = el("input"); cb.type = "checkbox"; cb.checked = centreState.selected.has(x.recipient_id); cb.setAttribute("aria-label", `Select ${x.name || x.email}`);
          cb.addEventListener("change", () => { cb.checked ? centreState.selected.add(x.recipient_id) : centreState.selected.delete(x.recipient_id); refresh(); });
          const c0 = el("td"); c0.append(cb);
          const c1 = el("td"); c1.append(el("b", "", when(x.due, x.timezone)), el("small", "", x.timezone.replace("_", " ") + " time"));
          const c2 = el("td"); c2.append(el("b", "", x.name || x.email), el("small", "", x.email));
          const c3 = el("td", "", x.subject); c3.append(el("small", "", x.ref));
          const c4 = el("td"); c4.append(x.state === "final" ? chip("Sender told", "violet") : chip(`Reminder ${x.number} of ${x.max}`));
          if (x.held) c4.append(el("small", "", `Waits: ${x.held}`)); else if (x.after_open) c4.append(el("small", "", "Moved: they opened it recently"));
          const a = el("td", "act");
          if (x.state !== "final") a.append(button("Skip", "button button-quiet button-sm", async () => { await act(x.recipient_id, "skip"); after("The next reminder is skipped."); }),
            button("Pause", "button button-quiet button-sm", async () => { const u = await askDate("Pause reminders", `For ${x.name || x.email} on this envelope.`, true); if (u === null) return; await act(x.recipient_id, "pause", u); after("Paused."); }));
          a.append(open(x.envelope_id));
          tr.append(c0, c1, c2, c3, c4, el("td", "", x.rule), a); tb.append(tr);
        });
        t.append(th, tb); const wrap = el("div", "rem-scroll"); wrap.append(t); body.append(wrap); refresh();
      } else if (centreState.tab === "held") {
        const rows = d.held.filter(match);
        if (!rows.length) return body.append(el("p", "rem-empty", "Nobody's reminders are paused or off."));
        const t = el("table", "rtable"); const hr = el("tr"); ["To", "Envelope", "State", "Why", ""].forEach(x => hr.append(el("th", "", x))); const th = el("thead"); th.append(hr); const tb = el("tbody");
        rows.forEach(x => {
          const tr = el("tr"); const [txt, tone] = STATE_TEXT[x.state] || [x.state, "grey"];
          const c1 = el("td"); c1.append(el("b", "", x.name || x.email), el("small", "", x.email));
          const c2 = el("td", "", x.subject); c2.append(el("small", "", x.ref));
          const c3 = el("td"); c3.append(chip(txt, tone)); if (x.paused_until) c3.append(el("small", "", x.paused_until >= FOREVER ? "until resumed" : `until ${day(x.paused_until)}`));
          const a = el("td", "act");
          if (x.state === "paused" && x.reason === "Paused for this person") a.append(button("Resume", "button button-quiet button-sm", async () => { await act(x.recipient_id, "resume"); after("Resumed."); }));
          if (x.state === "off") a.append(button("Switch back on", "button button-quiet button-sm", async () => { await act(x.recipient_id, "on"); after("Reminders are on again."); }));
          a.append(open(x.envelope_id));
          tr.append(c1, c2, c3, el("td", "", x.reason), a); tb.append(tr);
        });
        t.append(th, tb); const wrap = el("div", "rem-scroll"); wrap.append(t); body.append(wrap);
      } else if (centreState.tab === "sent") {
        const rows = d.sent.filter(match);
        if (!rows.length) return body.append(el("p", "rem-empty", "No reminders were sent in the last 30 days."));
        const KIND = {reminder: "Reminder", reminder_digest: "Combined reminder", expiring: "Expiration warning"};
        const STATE = {sent: ["Delivered to the mail server", "green"], queued: ["Waiting to go out", "grey"], sending: ["Sending", "grey"], failed: ["Not sent", "red"], bounced: ["Bounced", "red"], uncertain: ["Not confirmed", "amber"]};
        const t = el("table", "rtable"); const hr = el("tr"); ["When", "To", "Envelope", "Email", "Status", ""].forEach(x => hr.append(el("th", "", x))); const th = el("thead"); th.append(hr); const tb = el("tbody");
        rows.forEach(x => {
          const tr = el("tr"); const [s, tone] = STATE[x.state] || [x.state, "grey"]; const st = el("td"); st.append(chip(s, tone));
          const c2 = el("td", "", x.envelope_subject); c2.append(el("small", "", x.ref));
          const a = el("td", "act"); a.append(open(x.envelope_id));
          tr.append(el("td", "", when(x.sent_at || x.created)), el("td", "", x.to), c2, el("td", "", KIND[x.kind] || x.kind), st, a); tb.append(tr);
        });
        t.append(th, tb); const wrap = el("div", "rem-scroll"); wrap.append(t); body.append(wrap);
      } else {
        const rows = d.attention.filter(match);
        body.append(el("p", "rem-note rem-pad", "The mail server accepted these emails but never confirmed them. They are NOT sent again by themselves (that is what used to send people the same email again and again). Check with the recipient if you can, then mark each one delivered or send it once more."));
        if (!rows.length) return body.append(el("p", "rem-empty", "Nothing needs a look."));
        const t = el("table", "rtable"); const hr = el("tr"); ["Email", "To", "Handed to the mail server", "Why", ""].forEach(x => hr.append(el("th", "", x))); const th = el("thead"); th.append(hr); const tb = el("tbody");
        rows.forEach(x => {
          const tr = el("tr"); const c1 = el("td"); c1.append(el("b", "", x.subject), el("small", "", `${x.ref}${x.attachments ? ` · ${x.attachments} attachment${x.attachments === 1 ? "" : "s"}` : ""}`));
          const a = el("td", "act");
          a.append(button("Mark as delivered", "button button-quiet button-sm", async () => { await api(`/api/reminders/mail/${x.id}`, {action: "delivered"}); after("Marked as delivered."); }),
            button("Send once more", "button button-primary button-sm", async () => { if (!confirm(`Send "${x.subject}" to ${x.to} once more? They may receive it twice if the first one did arrive.`)) return; await api(`/api/reminders/mail/${x.id}`, {action: "send_again"}); after("It will be sent once more."); }),
            open(x.envelope_id));
          tr.append(c1, el("td", "", x.to), el("td", "", when(x.handed_at || x.created)), el("td", "", x.reason), a); tb.append(tr);
        });
        t.append(th, tb); const wrap = el("div", "rem-scroll"); wrap.append(t); body.append(wrap);
      }
    }
    paint();
  }

  // ================================================================== the Reminders card on an envelope
  function envelopeCard(e) {
    const c = el("section", "card rem-card"); const hd = el("div", "card-head"); hd.append(el("h2", "", "Reminders")); c.append(hd);
    const body = el("div"); body.append(el("p", "muted small", "Loading…")); c.append(body);
    const load = async (data) => {
      const d = data || await api(`/api/reminders/envelopes/${e.id}`);
      body.replaceChildren(); hd.replaceChildren(el("h2", "", "Reminders"));
      const top = el("div", "rem-card-top");
      const pick = select([["", `Company default (${(d.rules.find(r => r.id === d.default_rule) || {}).name || "Standard"})`], ...d.rules.map(r => [r.id, r.name])], d.chosen);
      pick.setAttribute("aria-label", "Reminder rule for this envelope");
      pick.addEventListener("change", () => api(`/api/reminders/envelopes/${e.id}`, {rule: pick.value}).then(x => { toast("Reminder rule changed."); load(x); }).catch(fail));
      top.append(pick, d.paused_until ? button("Resume all", "button button-quiet button-sm", async () => load(await api(`/api/reminders/envelopes/${e.id}`, {paused_until: null})))
        : button("Pause all", "button button-quiet button-sm", async () => { const u = await askDate("Pause reminders for this envelope", "No reminder goes to anyone on this envelope until then.", true); if (u === null) return; load(await api(`/api/reminders/envelopes/${e.id}`, {paused_until: u})); }));
      hd.append(top);
      body.append(el("p", "muted small rem-rule", `${d.rule.name}: ${d.rule.summary} · ${d.timezone.replace("_", " ")} time`));
      if (d.global_paused_until) body.append(el("div", "notice notice-warning", "All automatic reminders are paused (Admin › Reminders)."));
      if (d.paused_until) body.append(el("div", "notice notice-warning", d.paused_until >= FOREVER ? "Reminders are paused for this envelope until you resume them." : `Reminders are paused for this envelope until ${day(d.paused_until)}.`));
      if (!d.people.length) body.append(el("p", "muted small", "Nobody on this envelope is waiting to sign."));
      d.people.forEach(p => body.append(person(p, d, load)));
    };
    load().catch(x => body.replaceChildren(el("p", "rem-error", x.message)));
    return c;
  }
  function person(p, d, reload) {
    const row = el("div", "rem-person");
    const av = el("span", "rem-av", (p.name || p.email || "·").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join(""));
    const main = el("div"); const h = el("h4", "", p.name || p.email);
    const ROLE = {signer: "Signer", viewer: "Views a copy", cc: "Receives a copy", in_person: "Signs in person"};
    h.append(chip(ROLE[p.role] || p.role, "grey"));
    const STATE = {scheduled: null, final: ["All reminders sent", "violet"], done: ["All reminders sent", "grey"], off: ["Reminders off", "red"], paused: ["Paused", "amber"],
      opted_out: ["Do-not-remind list", "red"], undeliverable: ["Email undeliverable", "red"], rule_off: ["No reminders (rule Off)", "grey"], not_reminded: ["Not reminded", "grey"], waiting_turn: ["Waiting for their turn", "grey"]};
    if (STATE[p.state]) h.append(chip(...STATE[p.state]));
    main.append(h, el("div", "rem-em", p.email));
    const steps = el("ul", "rem-steps");
    p.steps.forEach(s => { const li = el("li"); li.append(el("span", `k ${s.kind}`, `${s.label}${s.at ? " · " + when(s.at, d.timezone) : ""}${s.note ? ` (${s.note})` : ""}`)); steps.append(li); });
    if (p.steps.length) main.append(steps);
    if (p.reason && !["scheduled"].includes(p.state)) main.append(el("p", "rem-note", p.reason + (p.paused_until ? (p.paused_until >= FOREVER ? " · until resumed" : ` · until ${day(p.paused_until)}`) : "")));
    const acts = el("div", "rem-pacts");
    const reachable = !["not_reminded", "waiting_turn", "undeliverable", "opted_out"].includes(p.state);
    if (reachable) {
      const lab = el("label", "rem-toggle"); const sw = el("input"); sw.type = "checkbox"; sw.checked = !p.reminders_off; sw.setAttribute("role", "switch");
      lab.append(el("span", "", "Reminders"), sw);
      sw.addEventListener("change", () => api(`/api/reminders/recipients/${p.id}`, {action: sw.checked ? "on" : "off"}).then(x => { toast(sw.checked ? "Reminders on." : "Reminders off for this person."); reload(x); }).catch(err => { sw.checked = !sw.checked; fail(err); }));
      acts.append(lab);
      const line = el("div", "row");
      const now = button("Remind now", "button button-quiet button-sm", async () => { reload(await api(`/api/reminders/recipients/${p.id}`, {action: "remind_now"})); toast("A reminder is on its way."); });
      if (p.remind_now_from || p.reminders_off) { now.disabled = true; now.title = p.reminders_off ? "Reminders are off for this person" : `Possible again from ${when(p.remind_now_from)} (they received an email less than 24 hours ago)`; }
      line.append(now);
      const items = [];
      if (p.state === "paused" && p.paused_until) items.push(["Resume", async () => reload(await api(`/api/reminders/recipients/${p.id}`, {action: "resume"}))]);
      else items.push(["Pause until a date…", async () => { const u = await askDate("Pause reminders", `For ${p.name || p.email} on this envelope.`, true); if (u === null) return; reload(await api(`/api/reminders/recipients/${p.id}`, {action: "pause", until: u})); }]);
      if (p.state === "scheduled") items.push(["Skip the next reminder", async () => { reload(await api(`/api/reminders/recipients/${p.id}`, {action: "skip"})); toast("The next reminder is skipped."); }]);
      if (d.can_manage_list) items.push(["Add to the do-not-remind list…", async () => { const r = await rules(); addOptout(r.companies, () => reload(), {name: p.name, email: p.email}); }]);
      const more = el("details", "rem-more"); const sum = el("summary", "button button-quiet button-sm", "More"); more.append(sum);
      const list = el("div", "rem-menu"); items.forEach(([t, fn]) => list.append(button(t, "rem-menu-item", async () => { more.open = false; await fn(); })));
      more.append(list); line.append(more);
      acts.append(line);
      if (p.remind_now_from && !p.reminders_off) acts.append(el("small", "rem-note", `Remind now: possible again ${when(p.remind_now_from)}`));
    } else if (p.state === "opted_out" && d.can_manage_list) {
      acts.append(button("Do-not-remind list", "button button-quiet button-sm", () => window.portalRoute?.("#/admin/reminders/optouts")));
    }
    row.append(av, main, acts);
    return row;
  }

  // ================================================================== for the send screen (esign.js)
  async function ruleOptions() { const r = await rules(); return r; }
  function ruleLabel(settings, data) {
    if (!data) return "";
    const id = settings?.reminder_rule || "";
    const rule = data.rules.find(r => r.id === id);
    return rule ? rule.name : "Company default";
  }

  window.tfscReminders = {envelopeCard, ruleOptions, ruleLabel, addOptout};
  window.remindersShowAdmin = tab => showAdmin(tab || "rules").catch(fail);
  window.remindersShowCentre = tab => showCentre(tab || "upcoming").catch(fail);
})();
