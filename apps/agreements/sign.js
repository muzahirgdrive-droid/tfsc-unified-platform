/* Signing page for recipients (no account needed). Everything shown comes from the server as data and is rendered as text. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const app = $("sign-app");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const S = {state: null, values: {}, pending: null, tab: "type", style: 0, ink: {"draw-signature": false, "draw-initials": false}, upload: "", attempted: false};
  const STYLES = [["SigA", "Mr Dafoe"], ["SigB", "Sacramento"], ["SigC", "Great Vibes"]];
  const LABELS = {signature: "Sign", initials: "Initial", date_signed: "Date signed", name: "Full name", email: "Email", company: "Company", title: "Title", text: "Text", checkbox: "", dropdown: "Select"};

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

  function message(title, body) {
    $("message-title").textContent = title; $("message-body").textContent = body;
    show("message-card", true);
    ["loading", "code-gate", "consent-banner", "intro-banner", "sender-message", "done-card", "top-actions", "guide", "error"].forEach(id => show(id, false));
    $("documents").replaceChildren();
  }

  function dateToday() {
    try { return new Intl.DateTimeFormat("en-US", {timeZone: S.state.timezone, month: "2-digit", day: "2-digit", year: "numeric"}).format(new Date()); }
    catch (_) { return new Date().toLocaleDateString("en-US"); }
  }

  // ------------------------------------------------------------------ rendering
  function render(state) {
    S.state = state;
    show("loading", false); error("");
    $("company").textContent = state.company_short || state.company;
    $("envelope-ref").textContent = state.ref ? `Envelope ${state.ref}` : "";
    document.title = `${state.subject} · ${state.company_short}`;
    const r = state.recipient;
    if (!state.verified) {
      show("code-gate", true); ["consent-banner", "intro-banner", "done-card", "top-actions", "guide"].forEach(id => show(id, false));
      $("documents").replaceChildren(); $("code").focus(); return;
    }
    show("code-gate", false);
    senderMessage(state);
    const closed = {declined: "This envelope was declined", voided: "This envelope was voided", expired: "This envelope has expired"}[state.envelope_status];
    if (closed && r.status !== "declined") {
      message(closed, state.envelope_status === "expired" ? `It is no longer available for signing. Contact ${state.sender_email} if you still need to sign.` : `No action is needed. Contact ${state.sender_email} with any questions.`);
      return;
    }
    if (r.status === "declined") { message("You declined to sign", `${state.sender} has been notified.`); return; }
    const needsConsent = state.can_act && !state.consent;
    show("consent-banner", needsConsent);
    show("top-actions", state.can_act && r.role === "signer" && state.consent);
    $("consent-continue").disabled = !$("consent-check").checked;
    if (needsConsent) { show("intro-banner", false); show("done-card", false); show("guide", false); $("documents").replaceChildren(); return; }
    if (state.completed) done("All parties have completed these documents.", "Download your copy below. It includes the Certificate of Completion.", true);
    else if (!state.can_act && r.role !== "cc") done("You have finished.", state.envelope_status === "completing" ? "The completed documents are being prepared. You will receive them by email shortly." : "When everyone has finished, you will receive an email with the completed documents.", false);
    else show("done-card", false);
    const intro = $("intro-banner");
    if (state.can_act && r.role === "signer") { $("intro-title").textContent = "Please review and complete."; $("intro-body").textContent = " Select Start to go to each field marked for you, then select Finish."; show("viewer-complete", false); show("intro-banner", true); }
    else if (state.can_act && r.role === "viewer") { $("intro-title").textContent = "Please review the documents."; $("intro-body").textContent = " When you have read them, select Complete."; show("viewer-complete", true); show("intro-banner", true); }
    else if (r.role === "cc" && !state.completed) { $("intro-title").textContent = "You received a copy of these documents."; $("intro-body").textContent = " No action is needed. You will receive the completed documents by email."; show("viewer-complete", false); show("intro-banner", true); }
    else intro.hidden = true;
    documents(state);
    refresh();
  }

  function senderMessage(state) {
    const box = $("sender-message");
    $("sender-line").textContent = `${state.sender} (${state.company}) sent you: ${state.subject}`;
    $("sender-text").textContent = state.message || "";
    $("sender-note").textContent = state.recipient.note ? `Note for you: ${state.recipient.note}` : "";
    $("sender-note").hidden = !state.recipient.note;
    box.hidden = false;
  }

  function done(title, body, completed) {
    $("done-title").textContent = title; $("done-body").textContent = body;
    const links = $("done-links"); links.replaceChildren();
    if (completed) links.append(link("completed", "Download completed documents", "btn btn-primary"));
    (S.state.documents || []).forEach(d => links.append(link(`document/${encodeURIComponent(d.id)}`, d.name, "btn btn-ghost")));
    show("done-card", true); show("guide", false); show("top-actions", false);
  }
  function link(href, text, cls) { const a = el("a", cls, text); a.href = href; a.setAttribute("download", ""); return a; }

  function documents(state) {
    const container = $("documents"); container.replaceChildren();
    const mine = state.fields || [], others = state.others || [];
    (state.documents || []).forEach(doc => {
      const section = el("section", "document");
      section.append(el("h2", "document-name", doc.name));
      doc.page_sizes.forEach((size, index) => {
        const number = index + 1;
        const page = el("div", "page");
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
        mine.filter(f => f.document_id === doc.id && f.page === number).forEach(f => place(field(f, state), f));
        section.append(page);
      });
      container.append(section);
    });
    scale();
  }

  function field(f, state) {
    const editable = state.can_act;
    const label = f.label || LABELS[f.kind];
    let node;
    if (f.kind === "signature" || f.kind === "initials") {
      node = el("button", `field sig${f.required ? " required" : ""}`);
      node.type = "button";
      node.disabled = !editable;
      node.addEventListener("click", () => toggleSignature(f));
      paintSignature(node, f);
    } else if (["date_signed", "name", "email"].includes(f.kind)) {
      node = el("div", "field auto", f.kind === "date_signed" ? (f.value || dateToday()) : f.kind === "name" ? state.recipient.name : state.recipient.email);
    } else if (f.kind === "checkbox") {
      node = el("label", `field check-field${f.required ? " required" : ""}`);
      const box = el("input"); box.type = "checkbox"; box.disabled = !editable; box.checked = f.value === "true" || S.values[f.id] === true;
      box.setAttribute("aria-label", label || "Checkbox");
      box.addEventListener("change", () => { S.values[f.id] = box.checked; refresh(); });
      S.values[f.id] = box.checked; node.append(box);
    } else if (f.kind === "dropdown") {
      node = el("select", `field input${f.required ? " required" : ""}`); node.disabled = !editable;
      node.append(new Option(label || "Select", ""));
      (f.options.choices || []).forEach(choice => node.append(new Option(choice, choice)));
      node.value = f.value || S.values[f.id] || "";
      node.addEventListener("change", () => { S.values[f.id] = node.value; refresh(); });
      node.setAttribute("aria-label", label || "Select");
    } else {
      node = el("input", `field input${f.required ? " required" : ""}`); node.maxLength = 500; node.placeholder = label; node.disabled = !editable;
      node.value = f.value || S.values[f.id] || "";
      node.setAttribute("aria-label", `${label}${f.required ? " (required)" : ""}`);
      node.addEventListener("input", () => { S.values[f.id] = node.value; refresh(); });
    }
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

  // ------------------------------------------------------------------ guided signing
  function missing() {
    return (S.state?.fields || []).filter(f => {
      const value = S.values[f.id];
      if (["date_signed", "name", "email"].includes(f.kind)) return false;
      if (f.kind === "checkbox") return f.required && value !== true;
      return f.required && !(typeof value === "string" ? value.trim() : value);
    });
  }
  function refresh() {
    if (!S.state || !S.state.can_act || S.state.recipient.role !== "signer") { show("guide", false); return; }
    const left = missing();
    $("finish").disabled = left.length > 0;
    document.querySelectorAll(".field.required").forEach(node => node.classList.toggle("missing", S.attempted && left.some(f => f.id === node.dataset.field)));
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
  async function finish() {
    S.attempted = true;
    const left = missing();
    if (left.length) { refresh(); error(`Complete the ${left.length} required field${left.length === 1 ? "" : "s"} first.`); next(true); return; }
    $("finish").disabled = true; $("guide").disabled = true;
    try {
      const values = {};
      (S.state.fields || []).forEach(f => { if (S.values[f.id] !== undefined) values[f.id] = S.values[f.id]; });
      render(await call("api/finish", {values}));
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
  $("viewer-complete").addEventListener("click", async () => {
    try { render(await call("api/acknowledge", {})); } catch (e) { if (e.message) error(e.message); }
  });

  function closeMenu() { $("more-menu").hidden = true; $("more").setAttribute("aria-expanded", "false"); }
  $("more").addEventListener("click", event => { event.stopPropagation(); const open = $("more-menu").hidden; $("more-menu").hidden = !open; $("more").setAttribute("aria-expanded", String(open)); });
  document.addEventListener("click", closeMenu);
  $("finish-later").addEventListener("click", () => { closeMenu(); message("Your progress is not saved", "You can come back and complete the documents at any time by opening the link in your email again, until the documents expire."); });
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
      const state = await call("api/adopt", payload);
      $("adopt-dialog").close();
      const pending = S.pending; S.pending = null;
      render(state);
      if (pending) { S.values[pending.id] = true; paintSignature(document.querySelector(`[data-field="${pending.id}"]`), pending); refresh(); next(false); }
    } catch (e) { if (e.message) fail(e.message); }
  });

  // ------------------------------------------------------------------ start
  const title = app.dataset.messageTitle, body = app.dataset.messageBody;
  if (title) { message(title, body); return; }
  call("api/state").then(render).catch(e => { show("loading", false); if (e.message) error(e.message); });
})();
