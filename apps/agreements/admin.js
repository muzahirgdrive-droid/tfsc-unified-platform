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
    const h = head("Admin", "Company settings", "Add a contracting company — its letterhead, address, governing law, signatory and services — or change who signs for an existing one. Changes apply to agreements generated from now on.");
    h.append(button("+ Add a company", () => companyForm(null), "button button-primary"));
    view.append(h);
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
    if (c.custom) {
      const row = el("div", "sig-tools"); row.style.marginTop = "6px";
      row.append(button("Edit company details", async () => companyForm((await api(`/api/admin/companies/custom/${encodeURIComponent(c.key)}`)).company), "button button-quiet button-sm"),
                 button("Remove", async () => { if (!confirm(`Hide ${c.short}? Its past agreements are kept; it will no longer be selectable for new ones.`)) return; await api(`/api/admin/companies/custom/${encodeURIComponent(c.key)}/active`, {active: false}); toast(`${c.short} removed.`); await companies(); }, "button button-ghost button-sm"));
      about.append(row);
    }
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

  // ---- Add / edit a contracting company -------------------------------------------------
  const readFile = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(new Error("The file could not be read.")); r.readAsDataURL(f); });
  function textarea(value, rows = 2) { const t = el("textarea"); t.rows = rows; t.value = value || ""; return t; }

  function companyForm(existing) {
    const editing = !!existing;
    const view = $("admin-view"); view.replaceChildren();
    const st = {letterhead: null, logo: null, signature: null};
    const h = head("Admin · Company settings", editing ? `Edit ${existing.short}` : "Add a contracting company",
      "New companies become selectable on Create agreement and Send. Agreements generate on this company's letterhead, address, governing law and signature. Existing companies and agreements are untouched.");
    h.append(button("← Back to companies", () => companies().catch(fail), "button button-quiet"));
    view.append(h);
    const err = el("div", "alert alert-error"); err.hidden = true; view.append(err);
    const showError = m => { err.textContent = m; err.hidden = false; err.scrollIntoView({behavior: "smooth", block: "center"}); };

    const name = input(existing?.name || "", "text", {maxLength: 200}), short = input(existing?.short || "", "text", {maxLength: 120});
    const display = input(existing?.display || "", "text", {maxLength: 200}), email = input(existing?.email || "", "email", {maxLength: 200});
    const address = textarea(existing?.address), state = input(existing?.governing_state || "", "text", {maxLength: 60});
    const tz = input(existing?.timezone || "America/New_York", "text", {maxLength: 60});
    const signatory = input(existing?.signatory || "", "text", {maxLength: 150}), title = input(existing?.title || "", "text", {maxLength: 150});
    const color = input(existing?.color || "#1D4277", "text", {maxLength: 9}), accent = input(existing?.accent || "#189BD2", "text", {maxLength: 9});
    const docCB = {}; ["baa", "services", "nda"].forEach(t => { docCB[t] = input("", "checkbox"); docCB[t].checked = existing ? (existing.doc_types || []).includes(t) : true; });
    const marginInputs = {}; ["top", "bottom", "left", "right"].forEach(k => { marginInputs[k] = input(existing?.margins?.[k] ?? "", "number", {step: "0.05", min: "0"}); });

    // Letterhead
    const lhNote = el("p", "field-note", editing ? "Leave empty to keep the current letterhead, or upload a new .docx to replace it." : "Upload the company letterhead (.docx). Keep the logo/artwork in the Word header or footer.");
    const lhInput = el("input"); lhInput.type = "file"; lhInput.accept = ".docx";
    lhInput.addEventListener("change", async () => { const f = lhInput.files[0]; if (!f) return;
      try { st.letterhead = await readFile(f); const d = await api("/api/admin/companies/parse-letterhead", {letterhead: st.letterhead});
        ["top", "bottom", "left", "right"].forEach(k => { marginInputs[k].value = d.margins[k]; });
        lhNote.textContent = `Letterhead read: ${d.page_width}in x ${d.page_height}in page, ${d.headers} header(s), ${d.footers} footer(s). Margins pre-filled — Preview a sample and raise the top margin if the text overlaps the logo.`;
      } catch (e) { showError(e.message); } });
    const logoInput = el("input"); logoInput.type = "file"; logoInput.accept = "image/png,image/jpeg";
    logoInput.addEventListener("change", async () => { const f = logoInput.files[0]; if (f) st.logo = await readFile(f); });
    const sigInput = el("input"); sigInput.type = "file"; sigInput.accept = "image/png,image/jpeg";
    const sigPreview = el("span", "small muted", existing?.has_signature ? "A signature is saved for this company." : "Optional. You can also add it later from the company card.");
    sigInput.addEventListener("change", async () => { const f = sigInput.files[0]; if (f) { st.signature = await readFile(f); sigPreview.textContent = "New signature ready to save."; } });

    // Services editor
    const servicesBox = el("div");
    function addService(s) {
      const row = el("div", "svc-row"); const header = el("div", "svc-head"); const strong = el("strong", "", s?.name || "New service");
      header.append(strong, button("Remove", () => row.remove(), "button button-ghost button-sm")); row.append(header);
      const nm = input(s?.name || "", "text", {maxLength: 160}); nm.addEventListener("input", () => { strong.textContent = nm.value || "New service"; });
      const cat = el("select"); [["recurring", "Recurring"], ["on_demand", "On demand"]].forEach(([v, t]) => cat.append(new Option(t, v))); cat.value = s?.category || "on_demand";
      const g1 = el("div", "field-grid"); g1.append(field("Service name", nm), field("Billing type", cat)); row.append(g1);
      const scope = textarea(s?.scope), resp = textarea(s?.responsibilities), excl = textarea(s?.exclusions);
      row.append(field("Scope / description of the service", scope));
      const g2 = el("div", "field-grid"); g2.append(field("Applicable responsibilities", resp), field("Exclusions / limits", excl)); row.append(g2);
      row._get = () => ({name: nm.value, category: cat.value, scope: scope.value, responsibilities: resp.value, exclusions: excl.value});
      servicesBox.append(row);
    }
    (existing?.services || []).forEach(addService); if (!existing) addService();

    function panel(step, eyebrow, titleText, ...body) {
      const p = el("section", "panel"); const heading = el("div", "panel-heading"); heading.append(el("div", "step", step));
      const t = el("div"); t.append(el("p", "eyebrow", eyebrow), el("h2", "", titleText)); heading.append(t); p.append(heading);
      const b = el("div", "panel-body"); body.forEach(x => b.append(x)); p.append(b); return p;
    }
    const grid2 = (...f) => { const g = el("div", "field-grid"); f.forEach(x => g.append(x)); return g; };

    const column = el("div", "form-column"); column.style.display = "grid"; column.style.gap = "18px";
    column.append(
      panel("1", "IDENTITY", "Company name", grid2(field("Legal name *", name), field("Short name *", short)), grid2(field("Display name (as printed)", display), field("Notice / reply-to email", email))),
      panel("2", "BRAND & LETTERHEAD", "Letterhead and logo", (() => { const w = el("div"); w.append(el("label", "", "Letterhead (.docx)"), lhInput, lhNote); return w; })(),
            grid2((() => { const w = el("div", "field"); w.append(el("label", "", "Company logo (optional)"), logoInput, el("p", "field-note", "PNG or JPEG. A wordmark is generated if you skip it.")); return w; })(),
                  grid2(field("Brand colour", color), field("Accent colour", accent)))),
      panel("3", "LEGAL & SIGNATORY", "Address, governing law and signature", field("Registered / notice address *", address),
            grid2(field("Governing law (US state) *", state, "Full state name, e.g. Delaware."), field("Time zone", tz, "e.g. America/New_York.")),
            grid2(field("Authorized signatory *", signatory), field("Title *", title)),
            (() => { const w = el("div", "field"); w.append(el("label", "", "Signature image (optional)"), sigInput, sigPreview); return w; })()),
      (() => { const p = panel("4", "SERVICES THIS COMPANY OFFERS", "Services, scope & responsibilities", el("p", "section-description", "Each service contributes its own scope, responsibilities and exclusions to the Services Agreement."), servicesBox);
               p.querySelector(".panel-body").append(button("+ Add a service", () => addService(), "button button-quiet add-custom")); return p; })(),
      panel("5", "DOCUMENTS", "Agreement types this company can produce", (() => { const w = el("div", "permission-options");
            [["baa", "Business Associate Agreement (BAA)"], ["services", "Services Agreement"], ["nda", "Mutual NDA"]].forEach(([k, label]) => { const l = el("label", "check-inline"); l.append(docCB[k], document.createTextNode(" " + label)); w.append(l); }); return w; })()),
      (() => { const details = el("details", "settings-details"); details.append(el("summary", "", "Advanced: page margins (inches)"));
               const dc = el("div", "details-content"); dc.append(el("p", "field-note", "Raise the top or bottom margin if the agreement text overlaps the letterhead's logo or footer. Preview a sample to check."),
                 grid2(field("Top", marginInputs.top), field("Bottom", marginInputs.bottom)), grid2(field("Left", marginInputs.left), field("Right", marginInputs.right))); details.append(dc); return details; })(),
    );

    function collect() {
      const margins = {}; ["top", "bottom", "left", "right"].forEach(k => { if (marginInputs[k].value !== "") margins[k] = Number(marginInputs[k].value); });
      const payload = {name: name.value, short: short.value, display: display.value, email: email.value, address: address.value,
        governing_state: state.value, timezone: tz.value, signatory: signatory.value, title: title.value, color: color.value, accent: accent.value,
        services: [...servicesBox.children].map(r => r._get()).filter(s => s.name.trim()), doc_types: ["baa", "services", "nda"].filter(t => docCB[t].checked), margins};
      if (st.letterhead) payload.letterhead = st.letterhead;
      if (st.logo) payload.logo = st.logo;
      if (st.signature) { payload.signature = st.signature; payload.signature_kind = "uploaded"; }
      return payload;
    }
    const actions = el("div", "step-actions");
    actions.append(button("← Back", () => companies().catch(fail), "button button-quiet"),
      button("Preview a sample", async () => { try { if (!st.letterhead) { showError("Upload a letterhead first, then Preview a sample."); return; }
        const d = await api("/api/admin/companies/preview", collect());
        const bin = atob(d.pdf.split(",")[1]); const bytes = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const url = URL.createObjectURL(new Blob([bytes], {type: "application/pdf"})); window.open(url, "_blank"); setTimeout(() => URL.revokeObjectURL(url), 60000); }
        catch (e) { showError(e.message); } }, "button button-quiet"),
      button(editing ? "Save changes" : "Create company", async () => { try {
        if (!editing && !st.letterhead) { showError("Upload the company letterhead (.docx)."); return; }
        await api(editing ? `/api/admin/companies/custom/${encodeURIComponent(existing.key)}` : "/api/admin/companies/new", collect());
        toast(editing ? "Company saved." : "Company created."); await companies();
      } catch (e) { showError(e.message); } }, "button button-primary"));

    const wrap = el("div"); wrap.append(column, actions); view.append(wrap);
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
