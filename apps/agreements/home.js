/* Home: agreement status at a glance (each count opens its list), the Start menu, figures and recent agreement activity. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let config = null, entity = "";
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function link(href, cls, text) { const a = el("a", cls, text); a.href = href; return a; }
  async function api(path) {
    const r = await fetch(path, {cache: "no-store"});
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { location.href = "/login"; throw new Error("Please sign in again."); }
    if (!r.ok) throw new Error(d.error || "The request failed.");
    return d;
  }
  function ago(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "Just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 172800) return "Yesterday";
    return new Date(ts * 1000).toLocaleDateString([], {month: "short", day: "numeric", year: new Date(ts * 1000).getFullYear() === new Date().getFullYear() ? undefined : "numeric"});
  }
  const ICONS = {sent: "↗", delivered: "◉", signed: "✓", reviewed: "✓", completed: "✓", declined: "✕", voided: "⊘", downloaded: "↓"};

  function startMenu(label = "Start", cls = "button button-primary button-lg") {
    const wrap = el("div", "start-menu");
    const button = el("button", cls); button.type = "button"; button.setAttribute("aria-haspopup", "true"); button.setAttribute("aria-expanded", "false");
    button.append(document.createTextNode(label), el("span", "chevron"));
    const list = el("div", "menu-list"); list.setAttribute("role", "menu"); list.hidden = true;
    [["#/send", "✉", "Send an envelope", "Upload documents or choose a client's agreements and send them for signature."],
     ["#/create", "＋", "Create an agreement", "BAA, Services Agreement or Mutual NDA on company letterhead."],
     ["#/templates", "▤", "Use a template", "Start from a saved envelope with its recipients and fields."],
     ["bulk", "⇉", "Bulk send", "Send one template to many people, each in their own envelope."]].forEach(([href, icon, title, text]) => {
      const a = link(href === "bulk" ? "#/agreements" : href, "", ""); a.setAttribute("role", "menuitem");
      const body = el("span"); body.append(el("strong", "", title), el("small", "", text));
      a.append(el("span", "menu-icon", icon), body);
      if (href === "bulk") a.addEventListener("click", event => { event.preventDefault(); list.hidden = true; window.portalRoute("#/agreements"); setTimeout(() => window.esignBulk?.(), 0); });
      list.append(a);
    });
    button.addEventListener("click", event => {
      event.stopPropagation();
      const open = list.hidden;
      document.querySelectorAll(".menu-list").forEach(m => { m.hidden = true; });
      list.hidden = !open; button.setAttribute("aria-expanded", String(open));
    });
    wrap.append(button, list);
    return wrap;
  }
  window.homeStartMenu = startMenu;

  async function show() {
    window.portalMark?.("#/home");
    const view = $("home-view");
    const d = await api(`/api/home?entity=${encodeURIComponent(entity)}`);
    view.replaceChildren();
    const first = (config?.user?.name || "").split(/\s+/)[0];
    const hero = el("section", "home-hero");
    const intro = el("div");
    intro.append(el("p", "eyebrow", "Agreement workspace"), el("h1", "", first ? `Welcome back, ${first}` : "Welcome back"),
      el("p", "", "Here is where your agreements stand. Select any count to open the list."));
    const actions = el("div", "hero-actions");
    actions.append(startMenu(), link("#/create", "button button-quiet button-lg", "Create an agreement"));
    intro.append(actions);
    const right = el("div");
    const strip = el("nav", "status-strip"); strip.setAttribute("aria-label", "Agreement status");
    [["action", "Action required", d.status.action], ["waiting", "Waiting for others", d.status.waiting], ["expiring", "Expiring soon", d.status.expiring],
     ["completed", "Completed", d.status.completed]].forEach(([box, label, count]) => {
      const tile = link(`#/agreements/${box}`, `status-tile${box === "action" && count ? " attention" : ""}`, "");
      tile.append(el("strong", "", count), el("span", "", label));
      tile.setAttribute("aria-label", `${label}: ${count}`);
      strip.append(tile);
    });
    right.append(strip, el("p", "home-period", "Completed: last 6 months"));
    hero.append(intro, right);
    view.append(hero);

    const head = el("div", "page-head"); head.style.margin = "24px 0 0";
    const title = el("div"); title.append(el("h2", "", "Last 6 months"));
    head.append(title);
    if ((config?.entities || []).length > 1) {
      const pick = el("select", "company-filter"); pick.setAttribute("aria-label", "Company");
      pick.append(new Option("All companies", ""));
      config.entities.forEach(e => pick.append(new Option(e.short || e.name, e.name)));
      pick.value = entity;
      pick.addEventListener("change", () => { entity = pick.value; show().catch(fail); });
      head.append(pick);
    }
    view.append(head);
    const t = d.totals;
    const kpis = el("div", "kpi-row");
    [["#/agreements/sent", "Documents sent", t.sent, "Envelopes sent for signature"],
     ["#/agreements/waiting", "Pending signature", t.pending_signatures, "Signers who have not signed yet"],
     ["#/agreements/completed", "Completed", t.completed, "Of the envelopes sent"],
     ["#/reports/insights", "Completion rate", t.completion_rate === null ? "—" : `${t.completion_rate}%`, "Sent and completed"],
     ["#/clients", "Agreements generated", t.generated, "BAA, Services and NDA packages"]].forEach(([href, label, value, note]) => {
      const k = link(href, "kpi", ""); k.append(el("span", "", label), el("strong", "", value), el("small", "", note)); kpis.append(k);
    });
    view.append(kpis);

    const grid = el("div", "home-grid");
    const feed = el("section", "card");
    const feedHead = el("div", "card-head"); feedHead.append(el("h2", "", "Recent agreement activity"), link("#/agreements/sent", "text-button", "View all"));
    feed.append(feedHead);
    if (!d.recent.length) feed.append(el("p", "empty-state", "Nothing yet. Send your first envelope from Start."));
    else {
      const list = el("ul", "activity-feed");
      d.recent.forEach(r => {
        const li = el("li");
        li.append(el("span", `feed-icon ${r.kind}`, ICONS[r.kind] || "•"));
        const body = el("div");
        const a = link(`#/envelope/${r.envelope_id}`, "", r.subject || "(no subject)");
        body.append(a, el("small", "", `${r.label}${r.kind === "sent" ? " by" : " ·"} ${r.who}`));
        const time = el("time", "", ago(r.at)); time.dateTime = new Date(r.at * 1000).toISOString(); time.title = new Date(r.at * 1000).toLocaleString();
        li.append(body, time);
        list.append(li);
      });
      feed.append(list);
    }
    const side = el("div"); side.style.display = "grid"; side.style.gap = "20px";
    const quick = el("section", "card");
    const quickHead = el("div", "card-head"); quickHead.append(el("h2", "", "Quick actions")); quick.append(quickHead);
    const qa = el("div", "quick-actions");
    [["#/send", "✉", "Send an envelope", "Upload or pick a client's agreements"], ["#/create", "＋", "Create an agreement", "BAA · Services · Mutual NDA"],
     ["#/clients", "◎", "Clients", "Profiles, documents and history"], ["#/reports", "▥", "Reports", "On screen or Excel"]].forEach(([href, icon, title, text]) => {
      const a = link(href, "quick-action", ""); const body = el("span"); body.append(el("strong", "", title), el("small", "", text));
      a.append(el("span", "menu-icon", icon), body); qa.append(a);
    });
    quick.append(qa);
    const help = el("section", "card");
    help.append(el("h3", "", "Need help?"));
    const email = config?.workspace?.help_email || "muzahir@scribealign.com";
    const p = el("p", "muted", "Please email "); const mail = link(`mailto:${email}`, "", email); p.append(mail, document.createTextNode(" with your concern."));
    help.append(p);
    side.append(quick, help);
    grid.append(feed, side);
    view.append(grid);
  }
  function fail(e) { const box = $("app-error"); box.textContent = e.message; box.hidden = false; }
  window.homeShow = () => show();
  window.homeReady = c => { config = c; };
})();
