/* Admin › Integrations (Super Admin): the read-only feed for TFS Onboarding and Client Success (OCS).
   Tokens (shown once, revocable), where the feed stands, and "Publish everything again". */
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
  const when = iso => iso ? new Date(iso).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : "—";
  function fail(e) { const box = $("app-error"); if (box) { box.textContent = e.message; box.hidden = false; } }
  function button(text, cls, fn) { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", () => Promise.resolve().then(fn).catch(fail)); return b; }
  function card(title, sub) {
    const c = el("section", "cat-card"); const h = el("header"); const t = el("div");
    t.append(el("h2", "", title)); if (sub) t.append(el("p", "", sub)); h.append(t); c.append(h);
    const body = el("div", "cat-body"); c.append(body); return [c, body];
  }
  function fact(label, value) { const f = el("div", "int-fact"); f.append(el("span", "", label), el("strong", "", value)); return f; }

  let shownToken = null;   // the token just created: shown once, then forgotten when the page is left

  function render(d) {
    const v = $("admin-view"); v.replaceChildren();
    const head = el("div", "page-head"); const t = el("div");
    t.append(el("p", "eyebrow", "Admin"), el("h1", "", "Integrations"),
      el("p", "page-sub", "TFS Onboarding and Client Success (OCS) reads every client with a signed agreement from here, by itself. It can only read; nothing in TFS Agreements can be changed through it."));
    head.append(t); v.append(head);

    if (shownToken) {
      const [c, b] = card("Copy this token now", "It is shown only once. Paste it into OCS (Admin › Integrations, or ocs.sh connect-agreements). If it is lost, create a new one and revoke this one.");
      c.classList.add("int-new");
      const row = el("div", "int-token"); const code = el("code", "", shownToken); row.append(code,
        button("Copy", "button button-primary", async () => { await navigator.clipboard.writeText(shownToken); window.tfscToast?.("Token copied."); }));
      b.append(row); v.append(c);
    }

    const [sc, sb] = card("The feed", d.enabled ? "Clients with a signed agreement are published within a minute of a change; every client is checked again once a day." :
      "The feed starts when the first token is created. Until then nothing is published and nothing runs.");
    const facts = el("div", "int-facts");
    facts.append(fact("Clients published", d.published_clients), fact("Newest change number", d.head_seq), fact("Last change published", when(d.last_published_at)),
      fact("Last full check", when(d.last_sweep_at)), fact("Behind by", d.lag_seconds === null ? "—" : d.lag_seconds < 90 ? `${d.lag_seconds} seconds` : `${Math.round(d.lag_seconds / 60)} minutes`),
      fact("Feed address", d.feed_path));
    sb.append(facts);
    if (d.retrying) sb.append(el("p", "cat-note", `${d.retrying} client(s) could not be published and are tried again every run. The server log has the reason.`));
    if (d.sweep_requested) sb.append(el("p", "cat-note", "Publishing everything again: it runs within a minute."));
    const acts = el("div", "cat-actions"); acts.append(el("span", "sp"));
    if (d.enabled) acts.append(button("Publish everything again", "button button-quiet", async () => {
      if (!confirm("Check every client again and publish any that differ from what OCS has? This only adds changes; nothing is removed.")) return;
      shownToken = null; render(await api("/api/admin/integrations/republish", {})); window.tfscToast?.("Queued. It runs within a minute.");
    }));
    sc.append(acts); v.append(sc);

    const [tc, tb] = card("Tokens", "Each token lets one system read the feed. Give OCS its own token; revoke it here at any time.");
    const form = el("div", "int-create"); const label = el("label", "", "Name"); const input = el("input"); input.id = "int-token-name"; input.maxLength = 100; input.placeholder = "OCS";
    label.htmlFor = input.id;
    form.append(label, input, button("Create token", "button button-primary", async () => {
      const name = input.value.trim() || "OCS"; const r = await api("/api/admin/integrations/tokens", {name});
      shownToken = r.token; render(r.view);
    }));
    tb.append(form);
    if (d.tokens.length) {
      const table = el("table", "cat-table"); const hr = el("tr"); ["Name", "Created", "Last used", "Status", ""].forEach(x => hr.append(el("th", "", x)));
      const thead = el("thead"); thead.append(hr); const tbody = el("tbody");
      d.tokens.forEach(k => {
        const row = el("tr"); const name = el("td"); name.append(el("b", "", k.name)); if (k.created_by) name.append(el("small", "int-by", ` by ${k.created_by}`));
        const used = el("td", "", k.last_used ? `${when(k.last_used)}${k.last_ip ? " · " + k.last_ip : ""}` : "Never");
        const status = el("td", k.revoked ? "cat-bad" : "cat-ok", k.revoked ? `Revoked ${when(k.revoked)}` : "Active");
        const act = el("td");
        if (!k.revoked) act.append(button("Revoke", "button button-quiet button-sm", async () => {
          if (!confirm(`Revoke "${k.name}"? Whatever uses it stops reading the feed at once.`)) return;
          shownToken = null; render(await api(`/api/admin/integrations/tokens/${encodeURIComponent(k.id)}/revoke`, {}));
        }));
        row.append(name, el("td", "", when(k.created)), used, status, act); tbody.append(row);
      });
      table.append(thead, tbody); const scroll = el("div", "int-scroll"); scroll.append(table); tb.append(scroll);
    } else tb.append(el("p", "cat-note", "No token yet."));
    v.append(tc);
  }

  window.integrationsShow = async () => {
    window.portalMark?.("#/admin/integrations");
    shownToken = null;
    render(await api("/api/admin/integrations"));
  };
  window.addEventListener("hashchange", () => { if (location.hash !== "#/admin/integrations") shownToken = null; });
})();
