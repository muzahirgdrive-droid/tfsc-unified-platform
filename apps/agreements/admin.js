/* Admin (Super Admin): Company settings (signatory, title and signature for each company; workspace settings) and Email trust. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  // Signature styles: OFL script fonts served by the portal (assets/fonts, licences alongside).
  const STYLES = [["Herr Von Muellerhoff", "herrvonmuellerhoff"], ["Mrs Saint Delafield", "mrssaintdelafield"], ["Monsieur La Doulaise", "monsieurladoulaise"], ["Great Vibes", "greatvibes"],
    ["Allura", "allura"], ["Alex Brush", "alexbrush"], ["Pinyon Script", "pinyonscript"], ["Parisienne", "parisienne"], ["Sacramento", "sacramento"], ["Dancing Script", "dancingscript"],
    ["La Belle Aurore", "labelleaurore"], ["Kristi", "kristi"], ["Cedarville Cursive", "cedarvillecursive"], ["Dawning of a New Day", "dawningofanewday"], ["Rouge Script", "rougescript"], ["Ephesis", "ephesis"]];
  const INK = "#1b2a6b";
  let config = null;  // eslint-disable-line no-unused-vars
  const loaded = new Set();
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function button(text, fn, cls = "button button-quiet") { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", () => Promise.resolve().then(fn).catch(fail)); return b; }
  function fail(e) { if (!e || !e.message) return; const box = $("app-error"); box.textContent = e.message; box.hidden = false; box.scrollIntoView({behavior: "smooth", block: "center"}); }
  function toast(text) { let t = document.querySelector(".toast"); if (!t) { t = el("div", "toast"); t.setAttribute("role", "status"); document.body.append(t); } t.textContent = text; t.hidden = false; setTimeout(() => { t.hidden = true; }, 4000); }
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  async function api(path, body) {
    const r = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: body === undefined ? {} : {"Content-Type": "application/json", "X-CSRF-Token": csrf()},
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
    let d = {}; try { d = await r.json(); } catch (_) { /* empty */ }
    if (r.status === 401) { location.href = "/login"; throw new Error("Please sign in again."); }
    if (!r.ok) throw new Error(d.error || `The request failed (${r.status}).`);
    return d;
  }
  function field(label, control, note) { const wrap = el("div", "field"); const l = el("label", "", label); control.id = control.id || `ad-${Math.random().toString(36).slice(2)}`; l.htmlFor = control.id; wrap.append(l, control); if (note) wrap.append(el("p", "field-note", note)); return wrap; }
  function input(value = "", type = "text", attrs = {}) { const i = el("input"); i.type = type; i.value = value ?? ""; Object.entries(attrs).forEach(([k, v]) => { i[k] = v; }); return i; }
  function head(eyebrow, title, sub) { const h = el("div", "page-head"); const left = el("div"); left.append(el("p", "eyebrow", eyebrow), el("h1", "", title), el("p", "page-sub", sub)); h.append(left); return h; }
  function dialog(title, build, confirmText, onConfirm) {
    const box = el("dialog", "portal-dialog"); box.style.width = "min(760px, calc(100vw - 32px))";
    const headEl = el("div", "dialog-heading"); headEl.append(el("h2", "", title));
    const close = el("button", "icon-button", "×"); close.type = "button"; close.setAttribute("aria-label", "Close"); close.addEventListener("click", () => box.close()); headEl.append(close);
    const body = el("div", "dialog-body"); const message = el("p", "notice notice-danger"); message.hidden = true;
    build(body); body.append(message);
    const foot = el("div", "dialog-footer"); const cancel = el("button", "button button-quiet", "Cancel"); cancel.type = "button"; cancel.addEventListener("click", () => box.close());
    const go = el("button", "button button-primary", confirmText); go.type = "button";
    go.addEventListener("click", async () => { go.disabled = true; message.hidden = true; try { await onConfirm(); box.close(); } catch (e) { message.textContent = e.message; message.hidden = false; } finally { go.disabled = false; } });
    foot.append(cancel, go); box.append(headEl, body, foot); document.body.append(box); box.addEventListener("close", () => box.remove()); box.showModal();
  }
  async function font(key) {
    const family = `Sig-${key}`;
    if (!loaded.has(key)) { const face = new FontFace(family, `url(/assets/fonts/sig-${key}.ttf)`); document.fonts.add(await face.load()); loaded.add(key); }
    return family;
  }
  function typed(text, family) {
    const canvas = document.createElement("canvas"); canvas.width = 1400; canvas.height = 360;
    const ctx = canvas.getContext("2d"); let size = 210;
    ctx.font = `${size}px "${family}"`;
    while (ctx.measureText(text).width > 1300 && size > 40) { size -= 8; ctx.font = `${size}px "${family}"`; }
    ctx.fillStyle = INK; ctx.textBaseline = "middle"; ctx.fillText(text, 50, 190);
    return canvas.toDataURL("image/png");
  }
  // A drawing pad with smoothed strokes (pointer events: mouse, pen and touch).
  function pad() {
    const canvas = el("canvas", "pad"); canvas.width = 1400; canvas.height = 360;
    const ctx = canvas.getContext("2d"); ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = INK;
    let last = null, drawn = false;
    const at = e => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height, e.pressure || 0.5]; };
    canvas.addEventListener("pointerdown", e => { canvas.setPointerCapture(e.pointerId); last = at(e); drawn = true; });
    canvas.addEventListener("pointermove", e => {
      if (!last) return; const p = at(e);
      ctx.lineWidth = 5 + p[2] * 4; ctx.beginPath(); ctx.moveTo(last[0], last[1]); ctx.quadraticCurveTo(last[0], last[1], (last[0] + p[0]) / 2, (last[1] + p[1]) / 2); ctx.lineTo(p[0], p[1]); ctx.stroke(); last = p;
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(t => canvas.addEventListener(t, () => { last = null; }));
    return {canvas, clear: () => { ctx.clearRect(0, 0, canvas.width, canvas.height); drawn = false; }, empty: () => !drawn, png: () => canvas.toDataURL("image/png")};
  }
  // The largest signature box on the agreement (2.3 x 0.96 in) at the preview's scale; a company prints at a share of it.
  const BOX = {w: 212, h: 88};
  function preview(src, label = "Signature:", scale = 1) {
    const box = el("div", "sig-preview");
    const line = el("div", "line"); line.append(el("span", "", label), el("span")); box.append(line, el("small", "", "As it prints on the agreement's signature line"));
    if (src) { const img = el("img"); img.alt = "Signature"; img.src = src; img.style.maxWidth = `${Math.round(BOX.w * scale)}px`; img.style.maxHeight = `${Math.round(BOX.h * scale)}px`; box.append(img); }
    else box.append(el("span", "sig-empty", "No signature saved"));
    return box;
  }

  // ------------------------------------------------------------------ Company settings
  async function companies() {
    window.portalMark?.("#/admin/companies");
    const d = await api("/api/admin/companies");
    const view = $("admin-view"); view.replaceChildren();
    view.append(head("Admin", "Company settings", "Who signs for each company, and the signature that prints on every agreement it issues. Changes apply to agreements generated from now on."));
    view.append(el("div", "notice notice-info", "Defaults: Naeem Abbas, Chief Executive Officer, for Scribe Align LLC and Extended Health Services Inc.; Danish Ather, Chief Operating Officer, for 24-7 Consultancy LLC. Change any of them below."));
    const cards = el("div", "company-cards"); cards.style.marginTop = "18px";
    d.companies.forEach(c => cards.append(companyCard(c)));
    view.append(cards);
    view.append(workspaceCard(d.workspace));
  }
  function companyCard(c) {
    const card = el("section", "company-card"); card.style.setProperty("--brand", c.color); card.style.setProperty("--brand-accent", c.accent);
    const about = el("div", "about");
    const logo = el("img", "logo"); logo.src = c.logo; logo.alt = c.short;
    about.append(logo, el("h2", "", c.entity));
    about.append(el("p", "muted small", c.mailbox ? `Mailbox: ${c.mailbox}` : "No mailbox connected yet"));
    const name = input(c.signatory, "text", {maxLength: 150}), title = input(c.title, "text", {maxLength: 150});
    about.append(field("Authorized signatory", name), field("Title", title, `Prints under the signature as “Title: …”. Default: ${c.default_signatory}, ${c.default_title}.`));
    const signing = el("div", "signing");
    signing.append(el("h3", "", "Signature on agreements"));
    const state = {image: null, kind: null, remove: false, scale: c.signature_scale};
    const src = c.has_signature ? `/api/companies/${encodeURIComponent(c.key)}/signature.png?v=${Date.now()}` : null;
    let current = src;
    let shown = preview(src, "Signature:", state.scale);
    signing.append(shown);
    const redraw = image => { current = image; const next = preview(image, "Signature:", state.scale); shown.replaceWith(next); shown = next; };
    const source = el("p", "small muted", c.signature_source === "settings" ? `Saved in Company settings${c.signature_kind ? ` (${c.signature_kind})` : ""}.` : c.signature_source === "installed" ? "Installed on the server. Saving a new one here replaces it." : "Generated agreements leave the company signing line blank until a signature is saved.");
    signing.append(source);
    const set = (image, kind) => { state.image = image; state.kind = kind; state.remove = false; redraw(image); source.textContent = "Not saved yet: select Save to use this signature."; };
    // Signature size: a share of the largest box, previewed on the line as it will print
    const size = input(String(Math.round(state.scale * 100)), "range", {min: 30, max: 100, step: 5});
    size.setAttribute("aria-label", "Signature size");
    const sizeValue = el("strong", "size-value", `${Math.round(state.scale * 100)} %`);
    const sizeRow = el("div", "sig-size"); sizeRow.append(el("span", "", "Size on agreements"), size, sizeValue);
    const defaultSize = Math.round(c.default_signature_scale * 100);
    const reset = button(`Default (${defaultSize} %)`, () => { size.value = String(defaultSize); size.dispatchEvent(new Event("input")); }, "button button-quiet button-sm");
    sizeRow.append(reset);
    size.addEventListener("input", () => { state.scale = Number(size.value) / 100; sizeValue.textContent = `${size.value} %`; redraw(current); });
    signing.append(sizeRow);
    const tools = el("div", "sig-tools");
    tools.append(button("Upload image", () => uploadDialog(set)), button("Draw", () => drawDialog(set)), button("Type a style", () => typeDialog(name.value || c.signatory, set)));
    if (c.has_signature) tools.append(button("Remove", () => { state.remove = true; state.image = null; redraw(null); source.textContent = "Not saved yet: select Save to remove the signature."; }, "button button-ghost"));
    signing.append(tools);
    const save = button(`Save ${c.short}`, async () => {
      const body = {signatory: name.value, title: title.value, signature_scale: state.scale};
      if (state.image) { body.signature = state.image; body.signature_kind = state.kind; }
      if (state.remove) body.remove_signature = true;
      await api(`/api/admin/companies/${encodeURIComponent(c.key)}`, body);
      toast(`${c.short} saved.`); await companies();
    }, "button button-primary");
    save.style.marginTop = "18px";
    signing.append(save);
    card.append(about, signing);
    return card;
  }
  function uploadDialog(set) {
    let cleaned = null;
    dialog("Upload a signature", body => {
      body.append(el("p", "muted", "A photo or scan of the signature on white paper (PNG or JPEG). The background is removed and the image is trimmed to the ink automatically."));
      const file = input("", "file", {accept: "image/png,image/jpeg"});
      const slot = el("div"); slot.append(preview(null));
      file.addEventListener("change", async () => {
        const f = file.files[0]; if (!f) return;
        if (f.size > 5000000) { fail(new Error("Choose an image smaller than 5 MB.")); return; }
        const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(f); });
        try { cleaned = (await api("/api/admin/companies/clean-signature", {image: data})).image; slot.replaceChildren(preview(cleaned)); }
        catch (e) { cleaned = null; slot.replaceChildren(el("p", "notice notice-danger", e.message)); }
      });
      body.append(field("Signature image", file), slot);
    }, "Use this signature", async () => { if (!cleaned) throw new Error("Choose an image first."); set(cleaned, "uploaded"); });
  }
  function drawDialog(set) {
    const p = pad();
    dialog("Draw the signature", body => {
      body.append(el("p", "muted", "Sign in the box with a mouse, a pen or your finger."), p.canvas);
      const row = el("div", "sig-tools"); row.style.marginTop = "10px"; row.append(button("Clear", () => p.clear())); body.append(row);
    }, "Use this signature", async () => { if (p.empty()) throw new Error("Draw the signature first."); set(p.png(), "drawn"); });
  }
  function typeDialog(name, set) {
    let choice = STYLES[0][1];
    const text = input(name, "text", {maxLength: 60});
    dialog("Type the signature", body => {
      const grid = el("div", "style-grid");
      for (const [label, key] of STYLES) {
        const b = el("button", "style-option", text.value || "Your name"); b.type = "button"; b.title = label; b.setAttribute("aria-pressed", String(key === choice)); b.setAttribute("aria-label", `Style ${label}`);
        b.addEventListener("click", () => { choice = key; grid.querySelectorAll(".style-option").forEach(x => x.setAttribute("aria-pressed", String(x === b))); });
        grid.append(b);
        font(key).then(family => { b.style.fontFamily = `"${family}"`; }).catch(() => {});
      }
      text.addEventListener("input", () => grid.querySelectorAll(".style-option").forEach(b => { b.textContent = text.value || "Your name"; }));
      body.append(field("Name", text), el("p", "small muted", "Choose a style"), grid);
    }, "Use this signature", async () => {
      const value = text.value.trim(); if (!value) throw new Error("Enter the name.");
      set(typed(value, await font(choice)), "typed");
    });
  }
  function workspaceCard(ws) {
    const card = el("section", "card"); card.style.marginTop = "22px";
    const h = el("div", "card-head"); h.append(el("h2", "", "Workspace, copyright and help")); card.append(h);
    const grid = el("div", "settings-grid");
    const portal = input(ws.portal_name, "text", {maxLength: 60}), holder = input(ws.copyright_holder, "text", {maxLength: 120}), help = input(ws.help_email, "email", {maxLength: 200}), trust = input(ws.trust_line, "text", {maxLength: 200});
    grid.append(field("Portal name", portal), field("Copyright holder", holder, "The footer reads “© year holder. All rights reserved.”"),
      field("Help email", help, "Help (?) tells people to email this address with their concern."), field("Security line on the signing page and emails", trust, "Describe only what the portal does. Have counsel review any compliance wording."));
    card.append(grid);
    const save = button("Save workspace settings", async () => { await api("/api/admin/workspace", {portal_name: portal.value, copyright_holder: holder.value, help_email: help.value, trust_line: trust.value}); toast("Workspace settings saved. Reload the page to see them everywhere."); }, "button button-primary");
    save.style.marginTop = "14px"; card.append(save);
    return card;
  }

  // ------------------------------------------------------------------ Email trust
  async function trust() {
    window.portalMark?.("#/admin/email-trust");
    const d = await api("/api/admin/companies");
    const view = $("admin-view"); view.replaceChildren();
    view.append(head("Admin", "Email trust & verified sender", "Emails that pass SPF, DKIM and DMARC reach the inbox and look genuine. With BIMI and a Verified Mark Certificate, Gmail shows the company logo and a blue verified tick next to the sender."));
    const steps = el("section", "card steps-card");
    steps.append(el("h2", "", "How to get the verified tick"));
    const ol = el("ol");
    [["Sender name.", " Under Admin › Company mailboxes, set the sender name to the company name (for example “Scribe Align LLC”). Gmail shows its first letter as the avatar until a logo is set."],
     ["Profile photo (quick win).", " Sign in to each compliance mailbox and set its profile photo to the company logo. Gmail shows it to many recipients right away."],
     ["SPF and DKIM.", " Publish SPF and turn on DKIM signing at your mail provider. Use the checks below to confirm both."],
     ["DMARC at enforcement.", " Publish DMARC, watch the reports for two to four weeks, then move to p=quarantine (or p=reject) at 100 %."],
     ["Logo and certificate.", " Prepare the logo as SVG Tiny PS and buy a Verified Mark Certificate (VMC) from DigiCert, Entrust or GlobalSign (it needs a registered trademark), or a Common Mark Certificate (shows the logo, without the tick)."],
     ["BIMI record.", " Publish default._bimi with the logo and certificate addresses. Gmail starts showing the logo and tick within a few days."]].forEach(([b, t]) => { const li = el("li"); li.append(el("strong", "", b), document.createTextNode(t)); ol.append(li); });
    steps.append(ol);
    view.append(steps);
    const grid = el("div", "company-cards"); grid.style.marginTop = "18px";
    d.companies.forEach(c => {
      const card = el("section", "card");
      const h = el("div", "card-head"); const left = el("div"); left.append(el("h2", "", c.entity), el("p", "muted small", c.mailbox || "No mailbox set")); h.append(left); card.append(h);
      const row = el("div", "settings-grid");
      const domain = input(c.mailbox ? c.mailbox.split("@")[1] : "", "text", {placeholder: "example.com"});
      const selector = input("google", "text", {placeholder: "google"});
      row.append(field("Domain", domain), field("DKIM selector", selector, "Google Workspace uses “google”; Zoho and others let you choose one."));
      card.append(row);
      const out = el("div", "trust-list"); out.style.marginTop = "14px";
      const check = button("Check now", async () => {
        out.replaceChildren(el("p", "muted", "Checking DNS…"));
        const r = await api("/api/admin/email-trust", {company: c.key, domain: domain.value.trim(), selector: selector.value.trim()});
        out.replaceChildren();
        r.checks.forEach(x => {
          const item = el("div", "trust-row");
          item.append(el("span", `trust-icon ${x.state}`, x.state === "ok" ? "✓" : x.state === "warning" ? "!" : "×"));
          const text = el("div"); text.append(el("strong", "", x.name), el("p", "small muted", x.advice));
          if (x.found) { text.append(el("small", "muted", "Published now:"), el("code", "", x.found)); }
          if (x.record) {
            text.append(el("small", "muted", `Add a ${x.record.type} record at “${x.record.host}”:`));
            const code = el("code", "", x.record.value); text.append(code);
          }
          item.append(text, el("span", `chip chip-${x.state === "ok" ? "success" : x.state === "warning" ? "warning" : "danger"}`, x.state === "ok" ? "Passing" : x.state === "warning" ? "Needs work" : "Missing"));
          if (x.record) { const copy = button("Copy record", async () => { await navigator.clipboard.writeText(x.record.value); toast("Record copied."); }, "button button-quiet button-sm"); copy.style.marginTop = "8px"; text.append(copy); }
          out.append(item);
        });
      }, "button button-primary");
      card.append(check, out);
      grid.append(card);
    });
    view.append(grid);
  }

  window.adminShow = page => (page === "email-trust" ? trust() : companies()).catch(fail);
  window.adminReady = c => { config = c; };
  window.signatureStyles = STYLES;
})();
