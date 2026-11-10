/* Signing page for recipients (no account needed). Everything shown comes from the server as data and is rendered as text. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const app = $("sign-app");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const S = {state: null, values: {}, copied: {}, active: null, pending: null, tab: "type", style: 0, initialsStyle: -1,
    ink: {"draw-signature": false, "draw-initials": false}, upload: "", uploadInitials: "", attempted: false, zoom: 1};
  // Signature styles: OFL script fonts served by the portal, loaded when the Adopt dialog opens.
  const STYLES = [["Herr Von Muellerhoff", "herrvonmuellerhoff"], ["Mrs Saint Delafield", "mrssaintdelafield"], ["Monsieur La Doulaise", "monsieurladoulaise"], ["Great Vibes", "greatvibes"],
    ["Allura", "allura"], ["Alex Brush", "alexbrush"], ["Pinyon Script", "pinyonscript"], ["Parisienne", "parisienne"], ["Sacramento", "sacramento"], ["Dancing Script", "dancingscript"],
    ["La Belle Aurore", "labelleaurore"], ["Kristi", "kristi"], ["Cedarville Cursive", "cedarvillecursive"], ["Dawning of a New Day", "dawningofanewday"], ["Rouge Script", "rougescript"], ["Ephesis", "ephesis"]];
  const INK = "#1b2a6b";
  const family = index => `Sig-${STYLES[index][1]}`;
  const fonts = new Map();
  function loadFont(index) {
    const key = STYLES[index][1];
    if (!fonts.has(key)) fonts.set(key, new FontFace(family(index), `url(/assets/fonts/sig-${key}.ttf)`).load().then(face => { document.fonts.add(face); return face; }));
    return fonts.get(key);
  }
  const LABELS = {signature: "Sign", initials: "Initial", date_signed: "Date signed", name: "Full name", first_name: "First name", last_name: "Last name", email: "Email",
    company: "Company", title: "Title", text: "Text", number: "Number", date: "MM/DD/YYYY", checkbox: "", radio: "", dropdown: "Select", attachment: "Attach a file", note: ""};
  const AUTO = ["date_signed", "name", "first_name", "last_name", "email"];
  const TEXT = ["company", "title", "text", "number", "date"];
  const COPY = ["title", "company"];          // entered once, copied into the other documents of the envelope
  const RULES = {letters: [/^[A-Za-z][A-Za-z .,'-]*$/, "letters only"], number: [/^-?\d{1,15}(\.\d{1,6})?$/, "a number"], email: [/^[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}$/, "an email address"],
    phone: [/^\+?1?[ .-]?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}$/, "a 10-digit phone number"], zip: [/^\d{5}(-\d{4})?$/, "a ZIP code (12345 or 12345-6789)"],
    date: [/^(0[1-9]|1[0-2])\/(0[1-9]|[12]\d|3[01])\/(19|20)\d{2}$/, "a date as MM/DD/YYYY"]};

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; }
  function svg(paths, cls = "pen") {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "2.2");
    s.setAttribute("aria-hidden", "true"); s.setAttribute("class", cls);
    paths.forEach(d => { const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", d); s.append(p); });
    return s;
  }
  const PEN = ["M4 20h4L19 9l-4-4L4 16v4z", "m13.5 6.5 4 4"];
  const CLIP = ["M20 11.5 12.2 19.3a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"];
  function show(id, on) { $(id).hidden = !on; }
  function error(message) { const box = $("error"); box.textContent = message; box.hidden = !message; if (message) box.scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center"}); }

  async function call(path, body) {
    const options = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify(body)};
    let response;
    // A restart for an update, or a moment without signal, should not cost the signer anything: the request never reached
    // the server (no connection, or the proxy answered 502/503), so it is sent again for up to about 25 seconds.
    for (const wait of [1000, 2000, 3000, 4000, 5000, 5000, 5000, 0]) {
      try {
        response = await fetch(path, {...options, credentials: "same-origin", cache: "no-store"});
        const fromProxy = (response.status === 502 || response.status === 503) && !(response.headers.get("content-type") || "").includes("application/json");
        if (!fromProxy || !wait) break;
      } catch (_) {
        if (!wait) throw new Error("You appear to be offline. Check your connection and try again. Nothing you entered has been lost.");
      }
      await new Promise(done => setTimeout(done, wait));
    }
    let data = {};
    try { data = await response.json(); } catch (_) { /* non-JSON error page */ }
    if (response.status === 401) { message("Your session has ended", "For your security, open the link from your email again."); throw new Error(""); }
    if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
    return data;
  }

  function railVisible(on) { $("rail").hidden = !on; document.body.classList.toggle("has-rail", on); if (!on) pages(false); }
  function message(title, body, links = []) {
    $("message-title").textContent = title; $("message-body").textContent = body;
    $("message-links").replaceChildren(...links);
    show("message-card", true);
    ["loading", "code-gate", "consent-banner", "intro-banner", "sender-message", "done-card", "top-actions", "guide", "error", "host-banner"].forEach(id => show(id, false));
    railVisible(false);
    $("documents").replaceChildren(); $("thumbs").replaceChildren();
  }

  function dateToday() {
    try { return new Intl.DateTimeFormat("en-US", {timeZone: S.state.timezone, month: "2-digit", day: "2-digit", year: "numeric"}).format(new Date()); }
    catch (_) { return new Date().toLocaleDateString("en-US"); }
  }
  // "Dr. Rebecca Moore, MD" -> ["Rebecca", "Moore"]: titles and credentials are not names (the server splits the same way).
  const HONORIFICS = ["dr", "mr", "mrs", "ms", "miss", "mx", "prof"];
  const CREDENTIALS = ["md", "do", "phd", "np", "pa", "pa-c", "dds", "dmd", "dpm", "dc", "od", "rn", "lcsw", "psyd", "jr", "sr", "ii", "iii", "iv", "esq", "cpa", "mba"];
  const nameParts = () => {
    let parts = (S.state.recipient.name || "").replace(/,/g, " ").split(/\s+/).filter(Boolean);
    const key = w => w.replace(/^\.+|\.+$/g, "").toLowerCase();
    while (parts.length > 1 && HONORIFICS.includes(key(parts[0]))) parts = parts.slice(1);
    while (parts.length > 1 && CREDENTIALS.includes(key(parts[parts.length - 1]))) parts = parts.slice(0, -1);
    return [parts[0] || "", parts.length > 1 ? parts[parts.length - 1] : ""];
  };
  function autoValue(f) {
    const [first, last] = nameParts();
    return {date_signed: f.value || dateToday(), name: S.state.recipient.name, first_name: first, last_name: last, email: S.state.recipient.email}[f.kind] || "";
  }
  const shortRef = () => (S.state?.signature_ref ? `${S.state.signature_ref}…` : "");
  const initialsOf = name => String(name || "").split(/\s+/).filter(Boolean).map(w => w[0].toUpperCase()).join("").slice(0, 3);
  // "Services Agreement - Lakeview Pediatrics.pdf" -> "Services Agreement"
  const shortDoc = name => String(name || "document").replace(/\.pdf$/i, "").split(/\s+[-–]\s+/)[0];
  const docOf = id => (S.state?.documents || []).find(d => d.id === id);
  const fieldOf = id => (S.state?.fields || []).find(f => f.id === id);
  const nodeOf = id => document.querySelector(`[data-field="${CSS.escape(id)}"]`);

  // ------------------------------------------------------------------ branding
  function brand(state) {
    const b = state.brand || {}, root = document.documentElement.style;
    if (b.color) root.setProperty("--brand", b.color);
    if (b.accent) root.setProperty("--accent", b.accent);
    if (b.dark) root.setProperty("--brand-dark", b.dark);
    else if (b.color) root.setProperty("--brand-dark", `color-mix(in srgb, ${b.color} 55%, #000)`);
    const company = state.company_display || state.company || "";
    if (b.logo) {
      $("company-logo").src = b.logo; $("company-logo").alt = company; show("logo-plate", true);
      $("consent-logo").src = b.logo; $("consent-logo").alt = company; show("consent-plate", true);
    }
    const portal = state.portal || {};
    if (portal.trust_line) $("trust-line").textContent = portal.trust_line;
    const test = !!state.test;
    show("test-strip", test); show("trust-strip", !test);
    $("footer-copyright").textContent = `© ${portal.year || new Date().getFullYear()} ${company.replace(/\.$/, "")}. All rights reserved.`;
    if (portal.help_email) { $("help-email").textContent = portal.help_email; $("help-email").href = `mailto:${portal.help_email}`; }
    $("help-sender").textContent = `Contact ${state.sender} at ${state.sender_email}, or reply to the email you received.`;
  }
  $("footer-help").addEventListener("click", () => $("help-dialog").showModal());

  // ------------------------------------------------------------------ rendering
  function render(state) {
    S.state = state;
    show("loading", false); show("message-card", false); error("");
    brand(state);
    $("company").textContent = state.company_short || state.company;
    $("envelope-ref").textContent = state.ref ? `Envelope ${state.ref}` : "";
    $("subject-line").textContent = state.subject || "";
    $("page-title").textContent = state.test ? "Review and sign · TEST" : "Review and sign";
    document.title = `${state.test ? "[TEST] " : ""}${state.subject} · ${state.company_short}`;
    const r = state.recipient;
    if (!state.verified) {
      $("page-title").textContent = "Access code required";
      show("code-gate", true); ["consent-banner", "intro-banner", "done-card", "top-actions", "guide", "sender-message"].forEach(id => show(id, false));
      railVisible(false); $("documents").replaceChildren(); $("code").focus(); return;
    }
    show("code-gate", false);
    hostBanner(state);
    if (r.status === "reassigned") { message("You assigned these documents to someone else", `${state.reassigned_to || "The person you chose"} received their own email. No further action is needed from you.`); return; }
    const closed = {declined: "This envelope was declined", voided: "This envelope was voided", expired: "This envelope has expired"}[state.envelope_status];
    if (closed && r.status !== "declined") {
      message(closed, state.envelope_status === "expired" ? `It is no longer available for signing. Contact ${state.sender_email} if you still need to sign.` : `No action is needed. Contact ${state.sender_email} with any questions.`);
      return;
    }
    if (r.status === "declined") { message("You declined to sign", `${state.sender} has been notified.`); return; }
    const needsConsent = state.can_act && !state.consent;
    const signing = state.can_act && ["signer", "in_person"].includes(r.role) && state.consent;
    show("top-actions", signing);
    $("reassign-open").hidden = !state.allow_reassign;
    if (needsConsent) {
      consentCard(state);
      ["intro-banner", "done-card", "guide", "sender-message"].forEach(id => show(id, false));
      railVisible(false); $("documents").replaceChildren(); $("thumbs").replaceChildren(); return;
    }
    show("consent-banner", false);
    senderCard(state, signing);
    if (state.completed) done("Completed by everyone", "All parties have signed. Download your copy below; it includes the Certificate of Completion.", true);
    else if (!state.can_act && r.role !== "cc") {
      if (state.in_person) done(`Thank you, ${nameParts()[0] || r.name}.`, `You have finished. Please hand the device back to ${state.host}.`, false, true);
      else done(`You're all set, ${nameParts()[0] || r.name}`, state.envelope_status === "completing"
        ? "The completed documents are being prepared. You will receive them by email shortly."
        : `You have finished. ${state.company_display || state.company} has been notified. When everyone has signed, you will receive the completed documents and the Certificate of Completion by email.`, false);
    }
    else show("done-card", false);
    if (state.can_act && r.role === "viewer") { $("intro-title").textContent = "Please review the documents."; $("intro-body").textContent = " When you have read them, select Complete."; show("viewer-complete", true); show("intro-banner", true); }
    else if (r.role === "cc" && !state.completed) { $("intro-title").textContent = "You received a copy of these documents."; $("intro-body").textContent = " No action is needed. You will receive the completed documents by email."; show("viewer-complete", false); show("intro-banner", true); }
    else show("intro-banner", false);
    S.values = {}; S.copied = {}; S.active = null;
    (state.fields || []).forEach(f => {
      const o = f.options || {};
      if (o.readonly) S.values[f.id] = o.prefill;
      else if (state.progress && state.progress[f.id] !== undefined) S.values[f.id] = state.progress[f.id];
      else if (f.kind === "attachment" && f.value) S.values[f.id] = f.value;
      else if (o.prefill && TEXT.includes(f.kind)) S.values[f.id] = o.prefill;
    });
    if (state.can_act) COPY.forEach(kind => { const source = order((state.fields || []).filter(f => f.kind === kind && String(S.values[f.id] || "").trim()))[0]; if (source) propagate(source, false); });
    documents(state);
    railVisible((state.documents || []).length > 0);
    $("guide").dataset.started = Object.keys(state.progress || {}).length ? "1" : "";
    refresh();
  }

  function consentCard(state) {
    $("consent-title").textContent = state.subject || "Documents to review";
    const company = state.company_display || state.company;
    $("consent-from").textContent = state.sender === company ? `From ${company}` : `From ${state.sender} · ${company}`;
    const list = $("consent-docs"); list.replaceChildren();
    (state.documents || []).forEach(d => {
      const li = el("li"); const text = el("div"); text.append(el("span", "", shortDoc(d.name)), el("small", "", `${d.pages} page${d.pages === 1 ? "" : "s"}`));
      li.append(el("span", "pdf-ic", "PDF"), text); list.append(li);
    });
    list.hidden = !(state.documents || []).length;
    $("consent-continue").disabled = !$("consent-check").checked;
    show("consent-banner", true);
  }

  function hostBanner(state) {
    const box = $("host-banner");
    if (!state.in_person || !state.can_act) { box.hidden = true; return; }
    box.replaceChildren(el("strong", "", `In-person signing for ${state.recipient.name}`), el("span", "", ` Hosted by ${state.host}. Hand this device to ${state.recipient.name} to review and sign.`));
    box.hidden = false;
  }

  function actionable(state) {
    return (state.fields || []).filter(f => f.required && !AUTO.includes(f.kind) && f.kind !== "note" && !(f.options || {}).readonly);
  }
  function senderCard(state, signing) {
    $("sender-avatar").textContent = initialsOf(state.sender) || "·";
    const line = $("sender-line"); const from = state.company_display || state.company;
    line.replaceChildren(document.createTextNode(state.sender), ...(state.sender === from ? [] : [el("span", "", ` · ${from}`)]));
    $("sender-text").textContent = state.message || `${state.subject}`;
    $("sender-note").textContent = state.recipient.note ? `Note for you: ${state.recipient.note}` : "";
    $("sender-note").hidden = !state.recipient.note;
    const meta = $("sender-meta"); meta.replaceChildren();
    const docs = (state.documents || []).length, todo = actionable(state).length;
    if (signing) {
      meta.append(el("b", "", `${todo} field${todo === 1 ? "" : "s"} for you`), document.createTextNode(`${docs} document${docs === 1 ? "" : "s"} · about ${Math.max(1, Math.round(todo * 10 / 60))} minute${todo > 9 ? "s" : ""}`));
      if (Object.keys(state.progress || {}).length) { meta.append(el("br")); meta.append(el("span", "welcome", "Welcome back · your entries were kept")); }
    } else meta.append(el("b", "", `${docs} document${docs === 1 ? "" : "s"}`),
      document.createTextNode(state.completed ? "Completed" : !state.can_act && state.recipient.role !== "cc" ? "You have finished" : state.role_label || ""));
    show("sender-message", true);
  }

  function done(title, body, completed, handBack = false) {
    $("done-title").textContent = title; $("done-body").textContent = body;
    $("page-title").textContent = completed ? "Documents completed" : "Signing complete";
    const rows = $("done-docs"); rows.replaceChildren();
    if (!handBack) (S.state.documents || []).forEach(d => {
      const a = el("a", "done-row"); a.href = `document/${encodeURIComponent(d.id)}`; a.setAttribute("download", "");
      const text = el("div"); text.append(el("b", "", shortDoc(d.name)), el("small", "", `${d.pages} page${d.pages === 1 ? "" : "s"}${completed ? " · completed" : ""}`));
      a.append(el("span", "pdf-ic", "PDF"), text, el("span", "go", "Download")); rows.append(a);
    });
    const links = $("done-links"); links.replaceChildren();
    if (completed) links.append(link("completed", "Download completed documents", "btn btn-primary"));
    if (handBack) { const back = el("a", "btn btn-primary", "Host: back to TFSC Agreements"); back.href = "/"; links.append(back); }
    else { const print = el("button", "btn btn-ghost", "Print"); print.type = "button"; print.addEventListener("click", () => window.print()); links.prepend(print); }
    $("done-note").textContent = S.state.ref ? `Envelope ${S.state.ref} · A tamper-evident record of every step is kept with the documents.` : "";
    show("done-card", true); show("guide", false); show("top-actions", false);
  }
  function link(href, text, cls) { const a = el("a", cls, text); a.href = href; a.setAttribute("download", ""); return a; }

  function documents(state) {
    const container = $("documents"); container.replaceChildren();
    const thumbs = $("thumbs"); thumbs.replaceChildren();
    const mine = state.fields || [], others = state.others || [], list = state.documents || [];
    list.forEach((doc, docIndex) => {
      const section = el("section", "document");
      const head = el("div", "doc-head"), title = el("div");
      const here = mine.filter(f => f.document_id === doc.id && f.required && !AUTO.includes(f.kind) && f.kind !== "note" && !(f.options || {}).readonly).length;
      title.append(el("small", "", list.length > 1 ? `Document ${docIndex + 1} of ${list.length}` : "Document"), el("h3", "", shortDoc(doc.name)));
      head.append(title, el("span", "chip", `${doc.pages} page${doc.pages === 1 ? "" : "s"}${state.can_act && here ? ` · ${here} field${here === 1 ? "" : "s"} for you` : ""}`));
      section.append(head);
      thumbs.append(el("p", "thumbs-doc", shortDoc(doc.name)));
      doc.page_sizes.forEach((size, index) => {
        const number = index + 1;
        const page = el("div", "page"); page.id = `page-${doc.id}-${number}`;
        page.style.aspectRatio = `${size[0]} / ${size[1]}`;
        page.dataset.width = size[0]; page.dataset.height = size[1];
        const image = el("img"); image.loading = number <= 2 ? "eager" : "lazy"; image.alt = `${doc.name}, page ${number} of ${doc.pages}`;
        image.src = `page/${encodeURIComponent(doc.id)}/${number}.png`;
        page.append(image);
        const place = (node, f) => {
          node.style.left = `${f.x / size[0] * 100}%`; node.style.top = `${f.y / size[1] * 100}%`;
          node.style.width = `${f.w / size[0] * 100}%`; node.style.height = `${f.h / size[1] * 100}%`;
          page.append(node);
        };
        others.filter(o => o.document_id === doc.id && o.page === number).forEach(o => {
          const box = el("div", `field other${o.done ? " other-done" : ""}`, o.done ? "" : o.name);
          box.setAttribute("aria-hidden", "true"); place(box, o);
        });
        const onPage = mine.filter(f => f.document_id === doc.id && f.page === number);
        onPage.forEach(f => place(field(f, state), f));
        section.append(page);
        const meta = el("div", "pmeta"); meta.append(el("span", "", doc.name), el("span", "", `Page ${number} of ${doc.pages}`)); section.append(meta);
        const thumb = el("button", `thumb${onPage.some(f => f.required) ? " thumb-fields" : ""}`); thumb.type = "button";
        const t = el("img"); t.loading = "lazy"; t.alt = ""; t.src = image.src; thumb.append(t, el("span", "", `Page ${number}`));
        thumb.setAttribute("aria-label", `${doc.name}, page ${number}${onPage.length ? ", has fields for you" : ""}`);
        thumb.addEventListener("click", () => { page.scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"}); if (window.innerWidth < 900) pages(false); });
        thumbs.append(thumb);
      });
      container.append(section);
    });
    Object.keys(S.copied).forEach(id => { const f = fieldOf(id); if (f) paintCopied(f); });
    applyVisibility();
    scale();
  }

  function field(f, state) {
    const editable = state.can_act && !(f.options || {}).readonly;
    const o = f.options || {};
    const label = f.label || o.tooltip || LABELS[f.kind];
    let node;
    const focus = () => { if (state.can_act && missing().some(x => x.id === f.id)) { $("guide").dataset.started = "1"; setActive(f.id); } };
    if (f.kind === "signature" || f.kind === "initials") {
      node = el("button", `field sig kind-${f.kind}${f.kind === "initials" ? " initials" : ""}${f.required ? " required" : ""}`);
      node.type = "button";
      node.disabled = !state.can_act;
      node.addEventListener("click", () => toggleSignature(f));
      paintSignature(node, f);
    } else if (AUTO.includes(f.kind)) {
      node = el("div", "field auto", autoValue(f));
      if (f.kind === "date_signed") node.title = "Today's date, filled in automatically when you sign";
    } else if (f.kind === "note") {
      node = el("div", "field note", o.text || ""); node.setAttribute("role", "note");
    } else if (f.kind === "checkbox") {
      node = el("label", `field check-field${f.required ? " required" : ""}`);
      const box = el("input"); box.type = "checkbox"; box.disabled = !editable; box.checked = f.value === "true" || S.values[f.id] === true || S.values[f.id] === "true";
      box.setAttribute("aria-label", label || "Checkbox");
      box.addEventListener("change", () => { S.values[f.id] = box.checked; applyVisibility(); refresh(); });
      box.addEventListener("focus", focus);
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
      box.addEventListener("focus", focus);
      if (box.checked) S.values[f.id] = true;
      node.append(box);
    } else if (f.kind === "dropdown") {
      node = el("select", `field input${f.required ? " required" : ""}`); node.disabled = !editable;
      node.append(new Option(f.label || "Select", ""));
      (o.choices || []).forEach(choice => node.append(new Option(choice, choice)));
      node.value = f.value || S.values[f.id] || "";
      node.classList.toggle("has-value", !!node.value);
      node.addEventListener("change", () => { S.values[f.id] = node.value; node.classList.toggle("has-value", !!node.value); applyVisibility(); refresh(); });
      node.addEventListener("focus", focus);
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
      node.value = S.values[f.id] !== undefined && S.values[f.id] !== null ? S.values[f.id] : (f.value || "");
      node.classList.toggle("has-value", !!node.value);
      node.setAttribute("aria-label", `${label}${f.required ? " (required)" : ""}`);
      node.addEventListener("input", () => {
        S.values[f.id] = node.value; node.classList.toggle("has-value", !!node.value); node.classList.toggle("invalid", !!invalid(f));
        if (S.copied[f.id]) { delete S.copied[f.id]; paintCopied(f); }        // the signer changed a copied value: it is theirs now
        fitField(node, f); fitHint(node, f, label);
        propagate(f, true);
        refresh();
      });
      node.addEventListener("focus", focus);
      node.addEventListener("focus", () => fitHint(node, f, label));
      node.addEventListener("blur", () => { fitHint(null, f); if (!node.classList.contains("fitted")) node.scrollLeft = 0; });
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
    if (signed && image) {
      const img = el("img"); img.src = image; img.alt = f.kind === "signature" ? "Your signature" : "Your initials";
      if (f.kind === "signature") {
        const stamp = el("span", "sig-stamp"); stamp.append(el("small", "", "Signed by:"), img, el("code", "", shortRef()));
        node.append(stamp);
      } else node.append(img);
      node.setAttribute("aria-label", `${f.kind === "signature" ? "Signature" : "Initials"} added. Select to remove.`);
    }
    else { node.append(svg(PEN), el("span", "", LABELS[f.kind])); node.setAttribute("aria-label", `${f.kind === "signature" ? "Signature" : "Initials"}${f.required ? " required" : ""}. Select to ${LABELS[f.kind].toLowerCase()}.`); }
  }

  function paintAttachment(node, f) {
    const has = S.values[f.id];
    node.replaceChildren(svg(CLIP), el("span", "", has ? "Attached ✓" : (f.label || "Attach a file")));
    node.title = has ? `${f.attachment_name || "File"} (select to replace)` : (f.options || {}).tooltip || "";
    node.classList.toggle("signed", !!has);
    node.setAttribute("aria-label", has ? `Attached ${f.attachment_name || "file"}. Select to replace.` : `${f.label || "Attachment"}${f.required ? " required" : ""}. Select to attach a file.`);
  }

  function pickAttachment(f) {
    const picker = el("input"); picker.type = "file"; picker.accept = "application/pdf,image/png,image/jpeg";
    picker.addEventListener("change", async () => {
      const file = picker.files[0]; if (!file) return;
      if (file.size > 10000000) { error("Attach a file smaller than 10 MB."); return; }
      const node = nodeOf(f.id); node?.classList.add("busy");
      try {
        const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.onerror = reject; r.readAsDataURL(file); });
        const values = {...S.values}, copied = {...S.copied}, started = $("guide").dataset.started;
        render(await call("api/attachment", {field_id: f.id, name: file.name, data}));
        Object.entries(values).forEach(([k, v]) => { if (k !== f.id && S.values[k] === undefined) S.values[k] = v; });
        S.copied = copied; $("guide").dataset.started = started;
        documents(S.state); refresh(); next(false);
      } catch (e) { if (e.message) error(e.message); }
      finally { nodeOf(f.id)?.classList.remove("busy"); }
    });
    picker.click();
  }

  function toggleSignature(f) {
    const adopted = f.kind === "signature" ? S.state.adopted.signature : S.state.adopted.initials;
    $("guide").dataset.started = "1";
    if (!adopted) { S.pending = f; setActive(f.id); openAdopt(); return; }
    S.values[f.id] = !S.values[f.id];
    paintSignature(nodeOf(f.id), f);
    refresh();
    if (S.values[f.id]) next(false); else setActive(f.id);
  }

  // ------------------------------------------------------------------ title and company: entered once, copied to the other documents
  function propagate(f, live) {
    if (!COPY.includes(f.kind) || !S.state?.can_act) return;
    const value = String(S.values[f.id] || "");
    (S.state.fields || []).forEach(g => {
      if (g.id === f.id || g.kind !== f.kind || g.document_id === f.document_id || (g.options || {}).readonly) return;
      const current = String(S.values[g.id] || "");
      if (current && S.copied[g.id] !== f.id) return;                 // the signer typed their own value there
      if (value.trim()) { S.values[g.id] = value; S.copied[g.id] = f.id; }
      else if (S.copied[g.id] === f.id) { S.values[g.id] = ""; delete S.copied[g.id]; }
      else return;
      if (live) {
        const node = nodeOf(g.id);
        if (node) { node.value = S.values[g.id] || ""; node.classList.toggle("has-value", !!node.value); node.classList.toggle("invalid", !!invalid(g)); fitField(node, g); }
        paintCopied(g);
      }
    });
  }
  function paintCopied(g) {
    const old = document.querySelector(`.copied[data-for="${CSS.escape(g.id)}"]`); if (old) old.remove();
    const source = S.copied[g.id] && fieldOf(S.copied[g.id]), node = nodeOf(g.id);
    if (!source || !node || !node.parentElement) return;
    const page = node.parentElement, h = Number(page.dataset.height) || 792, w = Number(page.dataset.width) || 612;
    const chip = el("span", "copied"); chip.dataset.for = g.id;
    chip.append(document.createTextNode(`✓ Copied from your ${shortDoc((docOf(source.document_id) || {}).name)}`));
    const edit = el("button", "", "Edit"); edit.type = "button";
    edit.addEventListener("click", () => { node.focus(); node.select?.(); });
    chip.append(edit);
    chip.style.left = `${g.x / w * 100}%`; chip.style.top = `calc(${(g.y + g.h) / h * 100}% + 3px)`;
    chip.style.maxWidth = `${Math.max(20, (w - g.x - 8) / w * 100)}%`;     // wraps on a phone instead of widening the page
    page.append(chip);
  }

  function scale() {
    document.querySelectorAll(".page").forEach(page => page.style.setProperty("--pt", `${page.clientWidth / Number(page.dataset.width)}px`));
    fitFields();
    placeGuide();
  }

  // ------------------------------------------------------------------ long typed values stay whole
  // The same rule as esign_pdf.fit_value on the signed PDF (constants from the server, state.fit): a value wider than its
  // box grows the box to the right, up to the page's right margin or the next field on the line (in page points, so zoom
  // and screen size do not change it), then the font shrinks instead of the text scrolling out of view.
  const FIT_DEFAULT = {right_margin: 54, min_scale: 0.6, gap: 2, floor: 4};
  let measurer = null;
  function textWidth(text, style) {
    measurer ||= document.createElement("canvas").getContext("2d");
    measurer.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    return measurer.measureText(text).width;
  }
  function fitLimit(f, pageWidth, rule) {
    let limit = pageWidth - rule.right_margin;
    [...(S.state?.fields || []), ...(S.state?.others || [])].forEach(o => {
      if (o === f || o.kind === "note" || o.document_id !== f.document_id || o.page !== f.page) return;
      if (o.x >= f.x + f.w - 0.5 && o.y < f.y + f.h && o.y + o.h > f.y) limit = Math.min(limit, o.x - rule.gap);
    });
    return Math.max(limit, f.x + f.w);
  }
  function fitField(node, f) {
    const page = node?.parentElement;
    if (!page || !page.classList.contains("page") || node.tagName === "SELECT") return;
    const rule = {...FIT_DEFAULT, ...(S.state?.fit || {})};
    const pageWidth = Number(page.dataset.width) || 612, ratio = page.clientWidth / pageWidth;
    node.style.width = `${f.w / pageWidth * 100}%`; node.style.fontSize = ""; node.classList.remove("fitted", "shrunk"); delete node.dataset.fitScale;
    const text = node.tagName === "INPUT" ? node.value : node.textContent;
    if (!text || !ratio) return;
    const style = getComputedStyle(node);
    const pad = ["paddingLeft", "paddingRight", "borderLeftWidth", "borderRightWidth"].reduce((n, k) => n + (parseFloat(style[k]) || 0), 0) + 2;
    const normal = parseFloat(style.fontSize), needed = textWidth(text, style) + pad;
    if (needed <= f.w * ratio) return;
    const room = (fitLimit(f, pageWidth, rule) - f.x) * ratio;
    node.style.width = `${Math.min(needed, room) / page.clientWidth * 100}%`;
    node.classList.add("fitted");
    if (needed > room) {
      const size = Math.max(rule.floor * ratio, normal * (room - pad) / (needed - pad));
      node.style.fontSize = `${size}px`; node.dataset.fitScale = (size / normal).toFixed(3);
      node.classList.add("shrunk");
    }
    if (document.activeElement !== node) node.scrollLeft = 0;
  }
  function fitFields() {
    (S.state?.fields || []).forEach(f => { const node = nodeOf(f.id); if (node && (node.matches("input.field") || node.matches(".field.auto"))) fitField(node, f); });
  }
  if (document.fonts?.ready) document.fonts.ready.then(() => fitFields()).catch(() => {});
  // While a fitted value is being typed, a note above the field confirms it is shown in full; when the font had to go below
  // min_scale of its normal size (a phone, a very long value), the note shows the value itself at a readable size.
  function fitHint(node, f, label) {
    document.querySelectorAll(".fit-hint").forEach(x => x.remove());
    if (!node || document.activeElement !== node || !node.classList.contains("fitted") || !node.parentElement) return;
    const page = node.parentElement, h = Number(page.dataset.height) || 792, w = Number(page.dataset.width) || 612;
    const rule = {...FIT_DEFAULT, ...(S.state?.fit || {})}, size = parseFloat(node.style.fontSize);
    const small = !!size && (size < 9 || Number(node.dataset.fitScale || 1) < rule.min_scale);
    const hint = el("span", `fit-hint${small ? " fit-hint-text" : ""}`, small ? node.value : `${label || "Text"} · ${node.value.length} characters · shown in full`);
    hint.setAttribute("aria-hidden", "true");
    hint.style.left = `${f.x / w * 100}%`; hint.style.top = `calc(${f.y / h * 100}% - 5px)`;
    hint.style.maxWidth = `${Math.max(20, (w - f.x - 8) / w * 100)}%`;     // stays on the page: no sideways scrolling
    page.append(hint);
  }
  window.addEventListener("resize", () => { scale(); if (S.active) setActive(S.active); });
  function setZoom(value) {
    S.zoom = Math.min(2, Math.max(0.6, Math.round(value * 10) / 10));
    document.documentElement.style.setProperty("--zoom", String(S.zoom));
    document.body.classList.toggle("zoomed-in", S.zoom > 1);
    $("zoom-level").textContent = `${Math.round(S.zoom * 100)}%`;
    requestAnimationFrame(() => { scale(); if (S.active) setActive(S.active); });
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
    map.forEach((on, id) => { const node = nodeOf(id); if (node) node.hidden = !on; const chip = document.querySelector(`.copied[data-for="${CSS.escape(id)}"]`); if (chip) chip.hidden = !on; });
    return map;
  }

  // ------------------------------------------------------------------ guided signing
  // Document by document; in each, the initials page by page first, then the signature, the title and the other fields by position.
  function order(list) {
    const at = new Map((S.state?.documents || []).map((d, i) => [d.id, i]));
    const rank = f => (at.has(f.document_id) ? at.get(f.document_id) : 999);
    return [...list].sort((a, b) => rank(a) - rank(b) || (a.kind === "initials" ? 0 : 1) - (b.kind === "initials" ? 0 : 1)
      || a.page - b.page || a.y - b.y || a.x - b.x);
  }
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
    Object.values(groups).forEach(g => { if (g.required && !g.chosen) out.push(order(g.fields)[0]); });
    return order(out);
  }
  const ACTION = f => f.kind === "initials" ? "Initial" : f.kind === "signature" ? "Sign" : f.kind === "attachment" ? "Attach"
    : ["checkbox", "radio", "dropdown"].includes(f.kind) ? "Select" : "Fill in";
  function tipFor(f) {
    const o = f.options || {}, doc = docOf(f.document_id) || {};
    const main = {initials: "Initial here", signature: "Sign here", title: "Your title or position", company: "Your company name", number: "Enter a number",
      date: "Enter a date", checkbox: "Check the box", radio: "Choose an option", dropdown: "Choose from the list", attachment: "Attach a file"}[f.kind] || f.label || "Fill in this field";
    const sub = o.tooltip || (f.kind === "title" ? "For example: Managing Member, Practice Manager"
      : f.kind === "signature" && !S.state.adopted.signature ? "Select to choose how your signature looks"
      : f.kind === "signature" ? "Select to place your signature" : `Page ${f.page} of ${doc.pages || "?"} · ${shortDoc(doc.name)}`);
    return [f.required ? "Required" : "Optional", (f.label && !["initials", "signature", "title"].includes(f.kind)) ? f.label : main, sub];
  }
  function setActive(id) {
    document.querySelectorAll(".field.active").forEach(n => n.classList.remove("active"));
    document.querySelectorAll(".tip").forEach(n => n.remove());
    S.active = id || null;
    const f = id && fieldOf(id), node = id && nodeOf(id);
    if (f && node && !node.hidden && node.parentElement) {
      node.classList.add("active");
      const [need, main, sub] = tipFor(f);
      const tip = el("div", "tip"), head = el("span"), small = el("small", "", sub);
      head.append(el("span", need === "Required" ? "req" : "opt", need), document.createTextNode(` · ${main}`));
      tip.append(head, small);
      const page = node.parentElement; page.append(tip);
      const below = TEXT.includes(f.kind) || f.kind === "dropdown" || node.offsetTop < tip.offsetHeight + 16;
      tip.classList.toggle("below", below);
      let left = node.offsetLeft - 4;
      left = Math.max(4, Math.min(left, page.clientWidth - tip.offsetWidth - 4));
      tip.style.left = `${left}px`;
      tip.style.top = `${below ? node.offsetTop + node.offsetHeight + 12 : node.offsetTop - tip.offsetHeight - 12}px`;
      if (S.copied[f.id]) { const chip = document.querySelector(`.copied[data-for="${CSS.escape(f.id)}"]`); if (chip && below) tip.style.top = `${node.offsetTop + node.offsetHeight + 34}px`; }
    }
    placeGuide();
  }
  function placeGuide() {
    const guide = $("guide"), ws = $("workspace"), docs = $("documents");
    if (guide.hidden || !docs.firstChild) return;
    const box = ws.getBoundingClientRect();
    const target = guide.dataset.started && S.active && nodeOf(S.active);
    let y;
    if (target && !target.hidden) { const r = target.getBoundingClientRect(); y = r.top - box.top + r.height / 2 - (guide.offsetHeight || 46) / 2; }
    else { const first = docs.querySelector(".page"); if (!first) return; y = first.getBoundingClientRect().top - box.top + 34; }
    ws.style.setProperty("--gy", `${Math.round(y)}px`);
    ws.style.setProperty("--gx", `${Math.round(docs.getBoundingClientRect().left - box.left)}px`);
  }
  if (window.ResizeObserver) new ResizeObserver(() => placeGuide()).observe($("workspace"));
  function refresh() {
    if (!S.state || !S.state.can_act || !["signer", "in_person"].includes(S.state.recipient.role)) { show("guide", false); return; }
    const left = missing();
    $("finish").disabled = left.length > 0;
    const map = visibleMap();
    const total = (S.state.fields || []).filter(f => map.get(f.id) && f.required && !AUTO.includes(f.kind) && f.kind !== "note" && !(f.options || {}).readonly).length;
    const doneCount = Math.max(0, total - left.length);
    $("progress").textContent = left.length ? `${doneCount} of ${total}` : "All done ✓";
    $("progress-bar").style.width = `${total ? doneCount / total * 100 : 100}%`;
    document.querySelectorAll(".field.required, .field.input").forEach(node => node.classList.toggle("missing", S.attempted && left.some(f => f.id === node.dataset.field)));
    const guide = $("guide");
    guide.hidden = false;
    const current = S.active && left.find(f => f.id === S.active);
    guide.textContent = !left.length ? "Finish" : !guide.dataset.started ? "Start" : current ? ACTION(current) : "Next";
    placeGuide();
  }
  function next(fromGuide = true) {
    const left = missing();
    const guide = $("guide");
    guide.dataset.started = "1";
    if (!left.length) { setActive(null); refresh(); if (fromGuide) finish(); return; }
    const current = fromGuide && S.active && left.find(f => f.id === S.active);
    const target = current || left[0];
    setActive(target.id);
    const node = nodeOf(target.id);
    if (node) {
      node.scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center"});
      node.classList.add("pulse"); setTimeout(() => node.classList.remove("pulse"), 1100);
      if (fromGuide && (node.matches("input, select") || node.querySelector("input"))) setTimeout(() => (node.matches("input, select") ? node : node.querySelector("input")).focus({preventScroll: true}), 380);
    }
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
      window.scrollTo({top: 0});
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
    try { render(await call("api/consent", {})); window.scrollTo({top: 0}); } catch (e) { if (e.message) error(e.message); }
  });
  const openDisclosure = () => { $("disclosure-text").replaceChildren(...(S.state?.ersd || "").split("\n\n").map(p => el("p", "", p))); $("disclosure-dialog").showModal(); };
  $("consent-read").addEventListener("click", openDisclosure);
  $("show-disclosure").addEventListener("click", () => { closeMenu(); openDisclosure(); });
  $("help-open").addEventListener("click", () => { closeMenu(); $("help-dialog").showModal(); });
  $("rail-help").addEventListener("click", () => $("help-dialog").showModal());
  $("viewer-complete").addEventListener("click", async () => {
    try { render(await call("api/acknowledge", {})); } catch (e) { if (e.message) error(e.message); }
  });

  function closeMenu() { $("more-menu").hidden = true; $("more").setAttribute("aria-expanded", "false"); }
  $("more").addEventListener("click", event => { event.stopPropagation(); const open = $("more-menu").hidden; $("more-menu").hidden = !open; $("more").setAttribute("aria-expanded", String(open)); });
  document.addEventListener("click", closeMenu);
  document.addEventListener("keydown", event => { if (event.key === "Escape") { closeMenu(); pages(false); } });
  function pages(open) {
    const on = open === undefined ? !document.body.classList.contains("pages-open") : open;
    document.body.classList.toggle("pages-open", on && !$("thumbs").hidden && $("thumbs").children.length > 0);
    $("rail-pages").setAttribute("aria-pressed", String(document.body.classList.contains("pages-open")));
  }
  $("thumbs").hidden = false;
  $("rail-pages").addEventListener("click", () => pages());
  $("pages-menu").addEventListener("click", () => { closeMenu(); pages(true); });
  $("finish-later").addEventListener("click", async () => {
    closeMenu();
    try {
      await call("api/save", {values: currentValues()});
      message("Your progress is saved", S.state.in_person ? "Open the envelope again from TFSC Agreements to continue." :
        "Open the link in your email again to continue where you left off, before the documents expire. Your signature is applied only when you select Finish.");
    } catch (e) { if (e.message) error(e.message); }
  });
  $("print").addEventListener("click", () => { closeMenu(); window.print(); });
  $("rail-print").addEventListener("click", () => window.print());
  function downloads() {
    closeMenu();
    const dialog = el("dialog", "dialog"); const form = el("form"); form.method = "dialog";
    const head = el("header", "dialog-head"), title = el("div"); title.append(el("h2", "", "Download documents"), el("p", "", "The documents as sent to you, as PDF files."));
    const x = el("button", "x", "×"); x.setAttribute("aria-label", "Close"); head.append(title, x);
    const body = el("div", "dialog-body");
    (S.state.documents || []).forEach(d => { const a = link(`document/${encodeURIComponent(d.id)}`, "", "download-row"); a.append(el("span", "pdf-ic", "PDF"), el("span", "", d.name)); body.append(a); });
    const actions = el("div", "dialog-actions"); const close = el("button", "btn btn-ds", "Close"); actions.append(close);
    form.append(head, body, actions); dialog.append(form); document.body.append(dialog); dialog.addEventListener("close", () => dialog.remove()); dialog.showModal();
  }
  $("download-original").addEventListener("click", downloads);
  $("rail-download").addEventListener("click", downloads);
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

  // ------------------------------------------------------------------ adopt signature and initials
  // Crop a canvas to its ink, with a little room around it, so stamps size the mark itself.
  function trimmed(canvas) {
    const ctx = canvas.getContext("2d"), {width, height} = canvas, data = ctx.getImageData(0, 0, width, height).data;
    let x0 = width, y0 = height, x1 = -1, y1 = -1;
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) if (data[(y * width + x) * 4 + 3] > 24) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    if (x1 < 0) return null;
    const pad = Math.max(4, Math.round((y1 - y0) * 0.06));
    x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad); x1 = Math.min(width - 1, x1 + pad); y1 = Math.min(height - 1, y1 + pad);
    const out = el("canvas"); out.width = x1 - x0 + 1; out.height = y1 - y0 + 1;
    out.getContext("2d").drawImage(canvas, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
    return out.toDataURL("image/png");
  }
  async function textImage(text, index, width, height) {
    await loadFont(index);
    const canvas = el("canvas"); canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext("2d");
    let size = height * 0.6;
    ctx.font = `${size}px "${family(index)}"`;
    while (ctx.measureText(text).width > width * 0.9 && size > 10) { size -= 2; ctx.font = `${size}px "${family(index)}"`; }
    ctx.fillStyle = INK; ctx.textBaseline = "middle"; ctx.fillText(text, width * 0.05, height * 0.55);
    return trimmed(canvas);
  }
  // An uploaded photo or scan: light paper made transparent, then trimmed (the server does the same again).
  async function cleanUpload(dataUrl) {
    const img = new Image(); img.src = dataUrl; await img.decode();
    const scale = Math.min(1, 1600 / img.width, 800 / img.height);
    const canvas = el("canvas"); canvas.width = Math.round(img.width * scale); canvas.height = Math.round(img.height * scale);
    const ctx = canvas.getContext("2d"); ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height), d = pixels.data;
    const levels = []; for (let i = 0; i < d.length; i += 16) levels.push(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    levels.sort((a, b) => a - b);
    const paper = levels[Math.floor(levels.length * 0.8)] || 255, edge = Math.max(40, paper - 28), ramp = Math.max(1, edge * 0.45);
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 255) continue;
      const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      d[i + 3] = lum >= edge ? 0 : Math.min(255, Math.round((edge - lum) * 255 / ramp));
    }
    ctx.putImageData(pixels, 0, 0);
    return trimmed(canvas);
  }
  function openAdopt() {
    $("adopt-name").value = S.state.recipient.name || "";
    $("adopt-initials").value = initialsOf($("adopt-name").value);
    $("adopt-error").hidden = true;
    $("preview-id").textContent = shortRef();
    const pick = $("initials-style");
    if (!pick.options.length) { pick.append(new Option("Same as the signature", "-1")); STYLES.forEach(([title], i) => pick.append(new Option(title, String(i)))); }
    styles(); $("adopt-dialog").showModal(); preview();
  }
  // Shrink a script name until it fits its card, so no style is cut off.
  function fit(node) {
    let size = 30; node.style.fontSize = `${size}px`;
    while (node.scrollWidth > node.clientWidth + 1 && size > 14) { size -= 1; node.style.fontSize = `${size}px`; }
  }
  function styles() {
    const box = $("styles"); box.replaceChildren();
    STYLES.forEach(([title], index) => {
      const option = el("label", "style-option");
      const radio = el("input"); radio.type = "radio"; radio.name = "style"; radio.checked = index === S.style; radio.setAttribute("aria-label", title);
      radio.addEventListener("change", () => { S.style = index; preview(); });
      const name = el("span", "style-name", $("adopt-name").value || "Your name");
      const initials = el("span", "style-initials", $("adopt-initials").value || "YN");
      loadFont(index).then(() => { name.style.fontFamily = initials.style.fontFamily = `"${family(index)}"`; requestAnimationFrame(() => fit(name)); }).catch(() => {});
      option.title = title;
      option.append(radio, name, initials); box.append(option);
    });
  }
  // What will be adopted: [signature data URL, initials data URL, how the signature was made].
  async function chosen() {
    const name = $("adopt-name").value.trim(), initials = $("adopt-initials").value.trim();
    const initialsStyle = S.initialsStyle >= 0 ? S.initialsStyle : S.style;
    if (S.tab === "draw") return [S.ink["draw-signature"] ? trimmed($("draw-signature")) : null, S.ink["draw-initials"] ? trimmed($("draw-initials")) : null, "drawn"];
    if (S.tab === "upload") return [S.upload || null, S.uploadInitials || (initials ? await textImage(initials, initialsStyle, 420, 220) : null), "uploaded"];
    return [name ? await textImage(name, S.style, 1200, 300) : null, initials ? await textImage(initials, initialsStyle, 420, 220) : null, "typed"];
  }
  let previewTimer = null;
  function preview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const [signature, initials] = await chosen();
        $("preview-signature").src = signature || ""; $("preview-signature").style.visibility = signature ? "visible" : "hidden";
        $("preview-initials").src = initials || ""; $("preview-initials").style.visibility = initials ? "visible" : "hidden";
      } catch (_) { /* preview only */ }
    }, 120);
  }
  $("adopt-name").addEventListener("input", () => { $("adopt-initials").value = initialsOf($("adopt-name").value); styles(); preview(); });
  $("adopt-initials").addEventListener("input", () => { styles(); preview(); });
  $("initials-style").addEventListener("change", () => { S.initialsStyle = Number($("initials-style").value); preview(); });
  document.querySelectorAll("[data-tab]").forEach(tab => tab.addEventListener("click", () => {
    S.tab = tab.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach(t => t.setAttribute("aria-selected", String(t === tab)));
    ["type", "draw", "upload"].forEach(name => show(`tab-${name}`, name === S.tab));
    preview();
  }));

  // Drawing pads: smoothed strokes whose width follows pen pressure.
  ["draw-signature", "draw-initials"].forEach(id => {
    const canvas = $(id), ctx = canvas.getContext("2d");
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = INK;
    let last = null;
    const point = event => { const box = canvas.getBoundingClientRect(); return [(event.clientX - box.left) * canvas.width / box.width, (event.clientY - box.top) * canvas.height / box.height, event.pressure || 0.5]; };
    canvas.addEventListener("pointerdown", event => { canvas.setPointerCapture(event.pointerId); last = point(event); });
    canvas.addEventListener("pointermove", event => {
      if (!last) return;
      const p = point(event);
      ctx.lineWidth = 3.5 + p[2] * 3;
      ctx.beginPath(); ctx.moveTo(last[0], last[1]); ctx.quadraticCurveTo(last[0], last[1], (last[0] + p[0]) / 2, (last[1] + p[1]) / 2); ctx.lineTo(p[0], p[1]); ctx.stroke();
      last = p; S.ink[id] = true;
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(name => canvas.addEventListener(name, () => { if (last) preview(); last = null; }));
  });
  document.querySelectorAll("[data-clear]").forEach(button => button.addEventListener("click", () => {
    const canvas = $(button.dataset.clear); canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height); S.ink[button.dataset.clear] = false; preview();
  }));
  [["upload-signature", "upload-preview", "upload"], ["upload-initials", "upload-initials-preview", "uploadInitials"]].forEach(([input, img, key]) => $(input).addEventListener("change", () => {
    const file = $(input).files[0]; S[key] = "";
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 5000000) { $("adopt-error").textContent = "Choose a PNG or JPEG image smaller than 5 MB."; $("adopt-error").hidden = false; return; }
    const reader = new FileReader();
    reader.onload = async () => {
      const cleaned = await cleanUpload(reader.result).catch(() => null);
      if (!cleaned) { $("adopt-error").textContent = "No signature was found in that image. Use dark ink on light paper, or draw it instead."; $("adopt-error").hidden = false; return; }
      S[key] = cleaned; $(img).src = cleaned; $(img).hidden = false; $("adopt-error").hidden = true; preview();
    };
    reader.readAsDataURL(file);
  }));

  $("adopt-confirm").addEventListener("click", async () => {
    const name = $("adopt-name").value.trim(), initials = $("adopt-initials").value.trim();
    const fail = text => { $("adopt-error").textContent = text; $("adopt-error").hidden = false; };
    if (!name || !initials) { fail("Enter your full name and initials."); return; }
    try {
      const [signature, initialsImage, kind] = await chosen();
      if (!signature) { fail(S.tab === "draw" ? "Draw your signature." : S.tab === "upload" ? "Choose an image of your signature." : "Enter your name."); return; }
      if (!initialsImage) { fail(S.tab === "draw" ? "Draw your initials." : "Enter your initials."); return; }
      const kept = {...S.values}, copied = {...S.copied};
      const state = await call("api/adopt", {kind, signature, initials: initialsImage});
      $("adopt-dialog").close();
      const pending = S.pending; S.pending = null;
      render(state);
      Object.entries(kept).forEach(([k, v]) => { S.values[k] = v; });
      S.copied = copied; $("guide").dataset.started = "1";
      documents(S.state);
      if (pending) { S.values[pending.id] = true; paintSignature(nodeOf(pending.id), pending); }
      refresh(); next(false);
    } catch (e) { if (e.message) fail(e.message); }
  });

  // ------------------------------------------------------------------ start
  const title = app.dataset.messageTitle, body = app.dataset.messageBody;
  if (title) { message(title, body); return; }
  call("api/state").then(render).catch(e => { show("loading", false); if (e.message) error(e.message); });
})();
