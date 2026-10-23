/* Signing page for recipients (no account needed). Everything shown comes from the server as data and is rendered as text. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const app = $("sign-app");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const S = {state: null, values: {}, pending: null, tab: "type", style: 0, ink: {"draw-signature": false, "draw-initials": false}, upload: "", attempted: false, zoom: 1};
  const STYLES = [["SigA", "Mr Dafoe"], ["SigB", "Sacramento"], ["SigC", "Great Vibes"]];
  const LABELS = {signature: "Sign", initials: "Initial", date_signed: "Date signed", name: "Full name", first_name: "First name", last_name: "Last name", email: "Email",
    company: "Company", title: "Title", text: "Text", number: "Number", date: "MM/DD/YYYY", checkbox: "", radio: "", dropdown: "Select", attachment: "Attach a file", note: ""};
  const AUTO = ["date_signed", "name", "first_name", "last_name", "email"];
  const TEXT = ["company", "title", "text", "number", "date"];
  const RULES = {letters: [/^[A-Za-z][A-Za-z .,'-]*$/, "letters only"], number: [/^-?\d{1,15}(\.\d{1,6})?$/, "a number"], email: [/^[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}$/, "an email address"],
    phone: [/^\+?1?[ .-]?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}$/, "a 10-digit phone number"], zip: [/^\d{5}(-\d{4})?$/, "a ZIP code (12345 or 12345-6789)"],
    date: [/^(0[1-9]|1[0-2])\/(0[1-9]|[12]\d|3[01])\/(19|20)\d{2}$/, "a date as MM/DD/YYYY"]};

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function show(id, on) { $(id).hidden = !on; }
  function error(message) { const box = $("error"); box.textContent = message; box.hidden = !message; if (message) box.scrollIntoView({behavior: "smooth", block: "center"}); }

  async function call(path, body) {
    const options = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify(body)};
    let response;
    try { response = await fetch(path, {...options, credentials: "same-origin", cache: "no-store"}); }
    catch (_) { throw new Error("You appear to be offline. Check your connection and try again."); }
    let data = {};
    try { data = await response.json(); } catch (_) { /* non-JSON error page */ }
    if (response.status === 401) { message("Your session has ended", "For your security, open the link from your email again."); throw new Error(""); }
    if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
    return data;
  }

  function message(title, body, links = []) {
    $("message-title").textContent = title; $("message-body").textContent = body;
    $("message-links").replaceChildren(...links);
    show("message-card", true);
    ["loading", "code-gate", "consent-banner", "intro-banner", "sender-message", "done-card", "top-actions", "guide", "error", "host-banner", "thumbs"].forEach(id => show(id, false));
    $("documents").replaceChildren();
  }

  function dateToday() {
    try { return new Intl.DateTimeFormat("en-US", {timeZone: S.state.timezone, month: "2-digit", day: "2-digit", year: "numeric"}).format(new Date()); }
    catch (_) { return new Date().toLocaleDateString("en-US"); }
  }
  const nameParts = () => { const parts = (S.state.recipient.name || "").split(/\s+/).filter(Boolean); return [parts[0] || "", parts.length > 1 ? parts[parts.length - 1] : ""]; };
  function autoValue(f) {
    const [first, last] = nameParts();
    return {date_signed: f.value || dateToday(), name: S.state.recipient.name, first_name: first, last_name: last, email: S.state.recipient.email}[f.kind] || "";
  }

  // ------------------------------------------------------------------ branding
  function brand(state) {
    const b = state.brand || {};
    if (b.color) document.documentElement.style.setProperty("--brand", b.color);
    if (b.accent) document.documentElement.style.setProperty("--accent", b.accent);
    if (b.logo) { const logo = $("company-logo"); logo.src = b.logo; logo.alt = state.company; logo.hidden = false; $("company").classList.add("visually-hidden"); }
  }

  // ------------------------------------------------------------------ rendering
  function render(state) {
    S.state = state;
    show("loading", false); error("");
    brand(state);
    $("company").textContent = state.company_short || state.company;
    $("envelope-ref").textContent = state.ref ? `Envelope ${state.ref}` : "";
    document.title = `${state.subject} · ${state.company_short}`;
    const r = state.recipient;
    if (!state.verified) {
      show("code-gate", true); ["consent-banner", "intro-banner", "done-card", "top-actions", "guide", "thumbs"].forEach(id => show(id, false));
      $("documents").replaceChildren(); $("code").focus(); return;
    }
    show("code-gate", false);
    senderMessage(state);
    hostBanner(state);
    if (r.status === "reassigned") { message("You assigned these documents to someone else", `${state.reassigned_to || "The person you chose"} received their own email. No further action is needed from you.`); return; }
    const closed = {declined: "This envelope was declined", voided: "This envelope was voided", expired: "This envelope has expired"}[state.envelope_status];
    if (closed && r.status !== "declined") {
      message(closed, state.envelope_status === "expired" ? `It is no longer available for signing. Contact ${state.sender_email} if you still need to sign.` : `No action is needed. Contact ${state.sender_email} with any questions.`);
      return;
    }
    if (r.status === "declined") { message("You declined to sign", `${state.sender} has been notified.`); return; }
    const needsConsent = state.can_act && !state.consent;
    show("consent-banner", needsConsent);
    const signing = state.can_act && ["signer", "in_person"].includes(r.role) && state.consent;
    show("top-actions", signing);
    $("reassign-open").hidden = !state.allow_reassign;
    $("consent-continue").disabled = !$("consent-check").checked;
    if (needsConsent) { show("intro-banner", false); show("done-card", false); show("guide", false); show("thumbs", false); $("documents").replaceChildren(); return; }
    if (state.completed) done("All parties have completed these documents.", "Download your copy below. It includes the Certificate of Completion.", true);
    else if (!state.can_act && r.role !== "cc") {
      if (state.in_person) done(`Thank you, ${r.name}.`, `You have finished. Please hand the device back to ${state.host}.`, false, true);
      else done("You have finished.", state.envelope_status === "completing" ? "The completed documents are being prepared. You will receive them by email shortly." : "When everyone has finished, you will receive an email with the completed documents.", false);
    }
    else show("done-card", false);
    const intro = $("intro-banner");
    if (signing) {
      const saved = Object.keys(state.progress || {}).length;
      $("intro-title").textContent = saved ? "Welcome back. Your earlier entries were kept." : "Please review the documents below.";
      $("intro-body").textContent = " Select Start to go to each field marked for you, then select Finish."; show("viewer-complete", false); show("intro-banner", true);
    }
    else if (state.can_act && r.role === "viewer") { $("intro-title").textContent = "Please review the documents."; $("intro-body").textContent = " When you have read them, select Complete."; show("viewer-complete", true); show("intro-banner", true); }
    else if (r.role === "cc" && !state.completed) { $("intro-title").textContent = "You received a copy of these documents."; $("intro-body").textContent = " No action is needed. You will receive the completed documents by email."; show("viewer-complete", false); show("intro-banner", true); }
    else intro.hidden = true;
    S.values = {};
    (state.fields || []).forEach(f => {
      const o = f.options || {};
      if (o.readonly) S.values[f.id] = o.prefill;
      else if (state.progress && state.progress[f.id] !== undefined) S.values[f.id] = state.progress[f.id];
      else if (f.kind === "attachment" && f.value) S.values[f.id] = f.value;
      else if (o.prefill && TEXT.includes(f.kind)) S.values[f.id] = o.prefill;
    });
    documents(state);
    refresh();
  }

  function hostBanner(state) {
    const box = $("host-banner");
    if (!state.in_person || !state.can_act) { box.hidden = true; return; }
    box.replaceChildren(el("strong", "", `In-person signing for ${state.recipient.name}`), el("span", "", ` Hosted by ${state.host}. Hand this device to ${state.recipient.name} to review and sign.`));
    box.hidden = false;
  }

  function senderMessage(state) {
    const box = $("sender-message");
    $("sender-line").textContent = `${state.sender} (${state.company}) sent you: ${state.subject}`;
    $("sender-text").textContent = state.message || "";
    $("sender-note").textContent = state.recipient.note ? `Note for you: ${state.recipient.note}` : "";
    $("sender-note").hidden = !state.recipient.note;
    box.hidden = false;
  }

  function done(title, body, completed, handBack = false) {
    $("done-title").textContent = title; $("done-body").textContent = body;
    const links = $("done-links"); links.replaceChildren();
    if (completed) links.append(link("completed", "Download completed documents", "btn btn-primary"));
    if (!handBack) (S.state.documents || []).forEach(d => links.append(link(`document/${encodeURIComponent(d.id)}`, d.name, "btn btn-ghost")));
    else { const back = el("a", "btn btn-primary", "Host: back to TFSC Agreements"); back.href = "/"; links.append(back); }
    show("done-card", true); show("guide", false); show("top-actions", false);
  }
  function link(href, text, cls) { const a = el("a", cls, text); a.href = href; a.setAttribute("download", ""); return a; }

  function documents(state) {
    const container = $("documents"); container.replaceChildren();
    const thumbs = $("thumbs"); thumbs.replaceChildren();
    const mine = state.fields || [], others = state.others || [];
    (state.documents || []).forEach(doc => {
      const section = el("section", "document");
      section.append(el("h2", "document-name", doc.name));
      thumbs.append(el("p", "thumbs-doc", doc.name));
      doc.page_sizes.forEach((size, index) => {
        const number = index + 1;
        const page = el("div", "page"); page.id = `page-${doc.id}-${number}`;
        page.style.aspectRatio = `${size[0]} / ${size[1]}`;
        page.dataset.width = size[0];
        const image = el("img"); image.loading = number <= 2 ? "eager" : "lazy"; image.alt = `${doc.name}, page ${number} of ${doc.pages}`;
        image.src = `page/${encodeURIComponent(doc.id)}/${number}.png`;
        page.append(image);
        const place = (node, f) => {
          node.style.left = `${f.x / size[0] * 100}%`; node.style.top = `${f.y / size[1] * 100}%`;
          node.style.width = `${f.w / size[0] * 100}%`; node.style.height = `${f.h / size[1] * 100}%`;
          page.append(node);
        };
        others.filter(o => o.document_id === doc.id && o.page === number).forEach(o => {
          const box = el("div", `field other${o.done ? " other-done" : ""}`, o.done ? (o.kind === "signature" ? `Signed by ${o.name}` : "") : o.name);
          box.setAttribute("aria-hidden", "true"); place(box, o);
        });
        const here = mine.filter(f => f.document_id === doc.id && f.page === number);
        here.forEach(f => place(field(f, state), f));
        section.append(page);
        const thumb = el("button", `thumb${here.some(f => f.required) ? " thumb-fields" : ""}`); thumb.type = "button";
        const t = el("img"); t.loading = "lazy"; t.alt = ""; t.src = image.src; thumb.append(t, el("span", "", `Page ${number}`));
        thumb.setAttribute("aria-label", `${doc.name}, page ${number}${here.length ? ", has fields for you" : ""}`);
        thumb.addEventListener("click", () => page.scrollIntoView({behavior: "smooth", block: "start"}));
        thumbs.append(thumb);
      });
      container.append(section);
    });
    thumbs.hidden = !(state.documents || []).length;
    applyVisibility();
    scale();
  }

  function field(f, state) {
    const editable = state.can_act && !(f.options || {}).readonly;
    const o = f.options || {};
    const label = f.label || o.tooltip || LABELS[f.kind];
    let node;
    if (f.kind === "signature" || f.kind === "initials") {
      node = el("button", `field sig${f.required ? " required" : ""}`);
      node.type = "button";
      node.disabled = !state.can_act;
      node.addEventListener("click", () => toggleSignature(f));
      paintSignature(node, f);
    } else if (AUTO.includes(f.kind)) {
      node = el("div", "field auto", autoValue(f));
    } else if (f.kind === "note") {
      node = el("div", "field note", o.text || ""); node.setAttribute("role", "note");
    } else if (f.kind === "checkbox") {
      node = el("label", `field check-field${f.required ? " required" : ""}`);
      const box = el("input"); box.type = "checkbox"; box.disabled = !editable; box.checked = f.value === "true" || S.values[f.id] === true || S.values[f.id] === "true";
      box.setAttribute("aria-label", label || "Checkbox");
      box.addEventListener("change", () => { S.values[f.id] = box.checked; applyVisibility(); refresh(); });
      S.values[f.id] = box.checked; node.append(box);
    } else if (f.kind === "radio") {
      node = el("label", `field radio-field${f.required ? " required" : ""}`);
      const box = el("input"); box.type = "radio"; box.name = `group-${o.group}`; box.disabled = !editable;
      box.checked = f.value === "true" || S.values[f.id] === true || S.values[f.id] === "true";
      box.setAttribute("aria-label", `${o.group}: ${o.choice}`); node.title = `${o.group}: ${o.choice}`;
      box.addEventListener("change", () => {
        (S.state.fields || []).filter(x => x.kind === "radio" && (x.options || {}).group === o.group).forEach(x => { S.values[x.id] = x.id === f.id; });
        applyVisibility(); refresh();
      });
      if (box.checked) S.values[f.id] = true;
      node.append(box);
    } else if (f.kind === "dropdown") {
      node = el("select", `field input${f.required ? " required" : ""}`); node.disabled = !editable;
      node.append(new Option(f.label || "Select", ""));
      (o.choices || []).forEach(choice => node.append(new Option(choice, choice)));
      node.value = f.value || S.values[f.id] || "";
      node.addEventListener("change", () => { S.values[f.id] = node.value; applyVisibility(); refresh(); });
      node.setAttribute("aria-label", label || "Select");
    } else if (f.kind === "attachment") {
      node = el("button", `field attach${f.required ? " required" : ""}`); node.type = "button"; node.disabled = !editable;
      paintAttachment(node, f);
      node.addEventListener("click", () => pickAttachment(f));
    } else {
      node = el("input", `field input${f.required ? " required" : ""}${o.readonly ? " readonly" : ""}`);
      node.maxLength = o.max_length || 4000; node.placeholder = f.kind === "date" ? "MM/DD/YYYY" : label; node.disabled = !editable;
      if (f.kind === "number") node.inputMode = "decimal";
      if (o.validation === "email") node.type = "email";
      if (o.validation === "phone") node.inputMode = "tel";
      node.value = f.value || S.values[f.id] || "";
      node.setAttribute("aria-label", `${label}${f.required ? " (required)" : ""}`);
      node.addEventListener("input", () => { S.values[f.id] = node.value; node.classList.toggle("invalid", !!invalid(f)); refresh(); });
    }
    if (o.tooltip) node.title = o.tooltip;
    node.dataset.field = f.id;
    return node;
  }

  function paintSignature(node, f) {
    const signed = f.value || S.values[f.id];
    const image = f.kind === "signature" ? S.state.adopted.signature : S.state.adopted.initials;
    node.replaceChildren();
    node.classList.toggle("signed", !!signed);
    if (signed && image) { const img = el("img"); img.src = image; img.alt = f.kind === "signature" ? "Your signature" : "Your initials"; node.append(img); node.setAttribute("aria-label", `${f.kind === "signature" ? "Signature" : "Initials"} added. Select to remove.`); }
    else { node.append(el("span", "sig-arrow", "↓"), el("span", "", LABELS[f.kind])); node.setAttribute("aria-label", `${f.kind === "signature" ? "Signature" : "Initials"}${f.required ? " required" : ""}. Select to ${LABELS[f.kind].toLowerCase()}.`); }
  }

  function paintAttachment(node, f) {
    const has = S.values[f.id];
    node.replaceChildren(el("span", "", has ? "Attached ✓" : `📎 ${f.label || "Attach a file"}`));
    node.title = has ? `${f.attachment_name || "File"} (select to replace)` : (f.options || {}).tooltip || "";
    node.classList.toggle("signed", !!has);
    node.setAttribute("aria-label", has ? `Attached ${f.attachment_name || "file"}. Select to replace.` : `${f.label || "Attachment"}${f.required ? " required" : ""}. Select to attach a file.`);
  }

  function pickAttachment(f) {
    const picker = el("input"); picker.type = "file"; picker.accept = "application/pdf,image/png,image/jpeg";
    picker.addEventListener("change", async () => {
      const file = picker.files[0]; if (!file) return;
      if (file.size > 10000000) { error("Attach a file smaller than 10 MB."); return; }
      const node = document.querySelector(`[data-field="${f.id}"]`); node.classList.add("busy");
      try {
        const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.onerror = reject; r.readAsDataURL(file); });
        const values = {...S.values};
        render(await call("api/attachment", {field_id: f.id, name: file.name, data}));
        Object.entries(values).forEach(([k, v]) => { if (k !== f.id && S.values[k] === undefined) S.values[k] = v; });
        documents(S.state); refresh();
      } catch (e) { if (e.message) error(e.message); }
      finally { node?.classList.remove("busy"); }
    });
    picker.click();
  }

  function toggleSignature(f) {
    const adopted = f.kind === "signature" ? S.state.adopted.signature : S.state.adopted.initials;
    if (!adopted) { S.pending = f; openAdopt(); return; }
    S.values[f.id] = !S.values[f.id];
    paintSignature(document.querySelector(`[data-field="${f.id}"]`), f);
    refresh();
    if (S.values[f.id]) next(false);
  }

  function scale() {
    document.querySelectorAll(".page").forEach(page => page.style.setProperty("--pt", `${page.clientWidth / Number(page.dataset.width)}px`));
  }
  window.addEventListener("resize", scale);
  function setZoom(value) {
    S.zoom = Math.min(2, Math.max(0.6, Math.round(value * 10) / 10));
    document.documentElement.style.setProperty("--zoom", String(S.zoom));
    $("zoom-level").textContent = `${Math.round(S.zoom * 100)}%`;
    requestAnimationFrame(scale);
  }
  $("zoom-in").addEventListener("click", () => setZoom(S.zoom + 0.1));
  $("zoom-out").addEventListener("click", () => setZoom(S.zoom - 0.1));

  // ------------------------------------------------------------------ conditional fields (the server applies the same rules at Finish)
  function visibleMap() {
    const fields = S.state?.fields || [], byId = new Map(fields.map(f => [f.id, f])), memo = new Map();
    const chosen = {};
    fields.filter(f => f.kind === "radio" && (S.values[f.id] === true || S.values[f.id] === "true")).forEach(f => { chosen[(f.options || {}).group] = (f.options || {}).choice; });
    const shown = (f, depth = 0) => {
      if (memo.has(f.id)) return memo.get(f.id);
      const c = (f.options || {}).condition, target = c && byId.get(c.field);
      let result = true;
      if (target && depth < 25) {
        if (!shown(target, depth + 1)) result = false;
        else {
          const current = target.kind === "checkbox" ? (S.values[target.id] === true || S.values[target.id] === "true" ? "checked" : "unchecked")
            : target.kind === "radio" ? chosen[(target.options || {}).group] || "" : String(S.values[target.id] || "");
          result = current === c.equals;
        }
      }
      memo.set(f.id, result); return result;
    };
    return new Map(fields.map(f => [f.id, shown(f)]));
  }
  function applyVisibility() {
    const map = visibleMap();
    map.forEach((on, id) => { const node = document.querySelector(`[data-field="${id}"]`); if (node) node.hidden = !on; });
    return map;
  }

  // ------------------------------------------------------------------ guided signing
  function invalid(f) {
    const o = f.options || {}, value = String(S.values[f.id] || "").trim();
    if (!value || !TEXT.includes(f.kind)) return "";
    if (o.max_length && value.length > o.max_length) return `use at most ${o.max_length} characters`;
    const rule = RULES[o.validation || (f.kind === "number" ? "number" : f.kind === "date" ? "date" : "")];
    return rule && !rule[0].test(value) ? `enter ${rule[1]}` : "";
  }
  function missing() {
    const map = visibleMap();
    const fields = (S.state?.fields || []).filter(f => map.get(f.id) && !AUTO.includes(f.kind) && f.kind !== "note" && !(f.options || {}).readonly);
    const out = [];
    const groups = {};
    fields.forEach(f => {
      const value = S.values[f.id];
      if (f.kind === "radio") { const g = (f.options || {}).group; groups[g] = groups[g] || {fields: [], required: false, chosen: false}; groups[g].fields.push(f); groups[g].required ||= f.required; groups[g].chosen ||= value === true || value === "true"; return; }
      if (invalid(f)) { out.push(f); return; }
      if (f.kind === "checkbox") { if (f.required && value !== true) out.push(f); return; }
      if (f.required && !(typeof value === "string" ? value.trim() : value)) out.push(f);
    });
    Object.values(groups).forEach(g => { if (g.required && !g.chosen) out.push(g.fields[0]); });
    return out;
  }
  function refresh() {
    if (!S.state || !S.state.can_act || !["signer", "in_person"].includes(S.state.recipient.role)) { show("guide", false); return; }
    const left = missing();
    $("finish").disabled = left.length > 0;
    document.querySelectorAll(".field.required, .field.input").forEach(node => node.classList.toggle("missing", S.attempted && left.some(f => f.id === node.dataset.field)));
    const guide = $("guide");
    guide.hidden = false;
    guide.textContent = left.length ? (guide.dataset.started ? "Next" : "Start") : "Finish";
    guide.classList.toggle("guide-finish", !left.length);
  }
  function next(scroll = true) {
    const left = missing();
    const guide = $("guide");
    guide.dataset.started = "1";
    if (!left.length) { refresh(); if (scroll) finish(); return; }
    const node = document.querySelector(`[data-field="${left[0].id}"]`);
    if (node) { node.scrollIntoView({behavior: "smooth", block: "center"}); node.classList.add("pulse"); setTimeout(() => node.classList.remove("pulse"), 1200); if (scroll) setTimeout(() => node.focus({preventScroll: true}), 350); }
    refresh();
  }
  $("guide").addEventListener("click", () => next(true));

  // ------------------------------------------------------------------ actions
  function currentValues() {
    const values = {};
    (S.state.fields || []).forEach(f => { if (S.values[f.id] !== undefined && S.values[f.id] !== null) values[f.id] = S.values[f.id]; });
    return values;
  }
  async function finish() {
    S.attempted = true;
    const left = missing();
    if (left.length) {
      refresh();
      const bad = left.find(f => invalid(f));
      error(bad ? `${bad.label || (bad.options || {}).tooltip || "A field"}: ${invalid(bad)}.` : `Complete the ${left.length} required field${left.length === 1 ? "" : "s"} first.`);
      next(true); return;
    }
    $("finish").disabled = true; $("guide").disabled = true;
    try {
      render(await call("api/finish", {values: currentValues()}));
      window.scrollTo({top: 0, behavior: "smooth"});
    } catch (e) { if (e.message) error(e.message); refresh(); }
    finally { $("guide").disabled = false; }
  }
  $("finish").addEventListener("click", finish);

  $("code-form").addEventListener("submit", async event => {
    event.preventDefault();
    try { render(await call("api/verify", {code: $("code").value})); }
    catch (e) { if (e.message) error(e.message); $("code").select(); }
  });
  $("consent-check").addEventListener("change", () => { $("consent-continue").disabled = !$("consent-check").checked; });
  $("consent-continue").addEventListener("click", async () => {
    try { render(await call("api/consent", {})); } catch (e) { if (e.message) error(e.message); }
  });
  const openDisclosure = () => { $("disclosure-text").replaceChildren(...(S.state?.ersd || "").split("\n\n").map(p => el("p", "", p))); $("disclosure-dialog").showModal(); };
  $("consent-read").addEventListener("click", openDisclosure);
  $("show-disclosure").addEventListener("click", () => { closeMenu(); openDisclosure(); });
  $("help-open").addEventListener("click", () => { closeMenu(); $("help-dialog").showModal(); });
  $("viewer-complete").addEventListener("click", async () => {
    try { render(await call("api/acknowledge", {})); } catch (e) { if (e.message) error(e.message); }
  });

  function closeMenu() { $("more-menu").hidden = true; $("more").setAttribute("aria-expanded", "false"); }
  $("more").addEventListener("click", event => { event.stopPropagation(); const open = $("more-menu").hidden; $("more-menu").hidden = !open; $("more").setAttribute("aria-expanded", String(open)); });
  document.addEventListener("click", closeMenu);
  $("finish-later").addEventListener("click", async () => {
    closeMenu();
    try {
      await call("api/save", {values: currentValues()});
      message("Your progress is saved", S.state.in_person ? "Open the envelope again from TFSC Agreements to continue." :
        "Open the link in your email again to continue where you left off, before the documents expire. Your signature is applied only when you select Finish.");
    } catch (e) { if (e.message) error(e.message); }
  });
  $("print").addEventListener("click", () => { closeMenu(); window.print(); });
  $("download-original").addEventListener("click", () => {
    closeMenu();
    const dialog = el("dialog", "dialog"); const form = el("form"); form.method = "dialog";
    form.append(el("h2", "", "Download documents"));
    (S.state.documents || []).forEach(d => form.append(link(`document/${encodeURIComponent(d.id)}`, d.name, "download-row")));
    const actions = el("div", "dialog-actions"); const close = el("button", "btn btn-primary", "Close"); actions.append(close); form.append(actions);
    dialog.append(form); document.body.append(dialog); dialog.addEventListener("close", () => dialog.remove()); dialog.showModal();
  });
  $("decline-open").addEventListener("click", () => { closeMenu(); $("decline-error").hidden = true; $("decline-dialog").showModal(); $("decline-reason").focus(); });
  $("decline-confirm").addEventListener("click", async () => {
    const reason = $("decline-reason").value.trim();
    if (!reason) { $("decline-error").textContent = "Enter a reason for declining."; $("decline-error").hidden = false; return; }
    try { render(await call("api/decline", {reason})); $("decline-dialog").close(); }
    catch (e) { $("decline-error").textContent = e.message; $("decline-error").hidden = !e.message; }
  });
  $("reassign-open").addEventListener("click", () => { closeMenu(); $("reassign-error").hidden = true; $("reassign-dialog").showModal(); $("reassign-name").focus(); });
  $("reassign-confirm").addEventListener("click", async () => {
    const fail = text => { $("reassign-error").textContent = text; $("reassign-error").hidden = !text; };
    const name = $("reassign-name").value.trim(), email = $("reassign-email").value.trim(), reason = $("reassign-reason").value.trim();
    if (!name || !email || !reason) { fail("Enter their name, their email and a message."); return; }
    try { render(await call("api/reassign", {name, email, reason})); $("reassign-dialog").close(); }
    catch (e) { fail(e.message); }
  });

  // ------------------------------------------------------------------ adopt signature
  function initialsOf(name) { return name.split(/\s+/).filter(Boolean).map(w => w[0].toUpperCase()).join("").slice(0, 3); }
  function openAdopt() {
    $("adopt-name").value = S.state.recipient.name || "";
    $("adopt-initials").value = initialsOf($("adopt-name").value);
    $("adopt-error").hidden = true;
    styles(); $("adopt-dialog").showModal();
  }
  function styles() {
    const box = $("styles"); box.replaceChildren();
    STYLES.forEach(([family, title], index) => {
      const option = el("label", "style-option");
      const radio = el("input"); radio.type = "radio"; radio.name = "style"; radio.checked = index === S.style; radio.setAttribute("aria-label", title);
      radio.addEventListener("change", () => { S.style = index; });
      const name = el("span", "style-name", $("adopt-name").value || "Your name"); name.style.fontFamily = family;
      const initials = el("span", "style-initials", $("adopt-initials").value || "YN"); initials.style.fontFamily = family;
      option.append(radio, name, initials); box.append(option);
    });
  }
  $("adopt-name").addEventListener("input", () => { $("adopt-initials").value = initialsOf($("adopt-name").value); styles(); });
  $("adopt-initials").addEventListener("input", styles);
  document.querySelectorAll("[data-tab]").forEach(tab => tab.addEventListener("click", () => {
    S.tab = tab.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach(t => t.setAttribute("aria-selected", String(t === tab)));
    ["type", "draw", "upload"].forEach(name => show(`tab-${name}`, name === S.tab));
  }));

  async function textImage(text, family, width, height) {
    await document.fonts.load(`80px ${family}`);
    const canvas = el("canvas"); canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext("2d");
    let size = height * 0.62;
    ctx.font = `${size}px ${family}`;
    while (ctx.measureText(text).width > width * 0.92 && size > 10) { size -= 2; ctx.font = `${size}px ${family}`; }
    ctx.fillStyle = "#14246b"; ctx.textBaseline = "middle"; ctx.fillText(text, width * 0.04, height * 0.55);
    return canvas.toDataURL("image/png");
  }

  ["draw-signature", "draw-initials"].forEach(id => {
    const canvas = $(id), ctx = canvas.getContext("2d");
    let drawing = false;
    const point = event => { const box = canvas.getBoundingClientRect(); return [(event.clientX - box.left) * canvas.width / box.width, (event.clientY - box.top) * canvas.height / box.height]; };
    canvas.addEventListener("pointerdown", event => { drawing = true; canvas.setPointerCapture(event.pointerId); const [x, y] = point(event); ctx.beginPath(); ctx.moveTo(x, y); });
    canvas.addEventListener("pointermove", event => {
      if (!drawing) return;
      const [x, y] = point(event);
      ctx.lineWidth = 3.2; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#14246b";
      ctx.lineTo(x, y); ctx.stroke(); S.ink[id] = true;
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(name => canvas.addEventListener(name, () => { drawing = false; }));
  });
  document.querySelectorAll("[data-clear]").forEach(button => button.addEventListener("click", () => {
    const canvas = $(button.dataset.clear); canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height); S.ink[button.dataset.clear] = false;
  }));
  $("upload-signature").addEventListener("change", () => {
    const file = $("upload-signature").files[0]; S.upload = "";
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 1500000) { $("adopt-error").textContent = "Choose a PNG or JPEG image smaller than 1.5 MB."; $("adopt-error").hidden = false; return; }
    const reader = new FileReader();
    reader.onload = () => { S.upload = reader.result; $("upload-preview").src = reader.result; $("upload-preview").hidden = false; $("adopt-error").hidden = true; };
    reader.readAsDataURL(file);
  });

  $("adopt-confirm").addEventListener("click", async () => {
    const name = $("adopt-name").value.trim(), initials = $("adopt-initials").value.trim();
    const fail = text => { $("adopt-error").textContent = text; $("adopt-error").hidden = false; };
    if (!name || !initials) { fail("Enter your full name and initials."); return; }
    try {
      const family = STYLES[S.style][0];
      let payload;
      if (S.tab === "draw") {
        if (!S.ink["draw-signature"] || !S.ink["draw-initials"]) { fail("Draw both your signature and your initials."); return; }
        payload = {kind: "drawn", signature: $("draw-signature").toDataURL("image/png"), initials: $("draw-initials").toDataURL("image/png")};
      } else if (S.tab === "upload") {
        if (!S.upload) { fail("Choose an image of your signature."); return; }
        payload = {kind: "uploaded", signature: S.upload, initials: await textImage(initials, family, 300, 200)};
      } else payload = {kind: "typed", signature: await textImage(name, family, 900, 240), initials: await textImage(initials, family, 300, 200)};
      const kept = {...S.values};
      const state = await call("api/adopt", payload);
      $("adopt-dialog").close();
      const pending = S.pending; S.pending = null;
      render(state);
      Object.entries(kept).forEach(([k, v]) => { S.values[k] = v; });
      documents(S.state);
      if (pending) { S.values[pending.id] = true; paintSignature(document.querySelector(`[data-field="${pending.id}"]`), pending); }
      refresh(); if (pending) next(false);
    } catch (e) { if (e.message) fail(e.message); }
  });

  // ------------------------------------------------------------------ start
  const title = app.dataset.messageTitle, body = app.dataset.messageBody;
  if (title) { message(title, body); return; }
  call("api/state").then(render).catch(e => { show("loading", false); if (e.message) error(e.message); });
})();
