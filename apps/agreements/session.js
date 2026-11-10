"use strict";
/* Session keeper for the staff portal.
   - Automatic sign-out: after the idle time set on the server (30 minutes by default) without a click, a key or a scroll in
     any open tab, the person is signed out. Two minutes before that a dialog counts down and offers "Stay signed in".
   - While somebody is working (typing a long agreement makes no request for a long time) the server session is kept alive.
   - When a session has ended anyway, the sign-in page opens and afterwards returns to the same screen; an agreement being
     prepared in the builder is kept in this tab and put back. */
(() => {
  const ACTIVE = "tfsc-last-activity", SNAPSHOT = "tfsc-builder-snapshot", WARN = 120;
  const send = window.fetch.bind(window);
  let idle = 1800, last = Date.now(), lastServer = Date.now(), leaving = false, dialog = null, countdown = null;
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const seen = () => { let shared = 0; try { shared = Number(localStorage.getItem(ACTIVE)) || 0; } catch (e) { /* private mode */ } return Math.max(last, shared); };

  function signIn(reason) {
    if (leaving) return;
    leaving = true; window.tfscLeaving = true;
    try {
      const snap = window.tfscBuilder && window.tfscBuilder.unsaved() ? window.tfscBuilder.snapshot() : null;
      if (snap) sessionStorage.setItem(SNAPSHOT, JSON.stringify({at: Date.now(), data: snap}));
    } catch (e) { /* storage unavailable: the page still returns to the same screen */ }
    location.href = "/login" + (reason ? "?reason=" + reason : "") + (location.hash || "");
  }
  window.tfscSignIn = signIn;
  window.tfscTakeSnapshot = () => {
    try {
      const raw = sessionStorage.getItem(SNAPSHOT); sessionStorage.removeItem(SNAPSHOT);
      const snap = raw ? JSON.parse(raw) : null;
      return snap && Date.now() - snap.at < 4 * 3600 * 1000 ? snap.data : null;
    } catch (e) { return null; }
  };

  // Every answer from the application passes here: a 401 means the session is over.
  window.fetch = async (input, init = {}) => {
    const response = await send(input, init);
    try {
      const url = new URL(typeof input === "string" ? input : input.url, location.href);
      if (url.origin === location.origin && url.pathname.startsWith("/api/")) {
        if (response.status === 401 && !/^\/api\/auth\/(login|setup|status)/.test(url.pathname)) signIn("expired");
        else if (response.ok) lastServer = Date.now();
      }
    } catch (e) { /* not a URL we know: leave the response alone */ }
    return response;
  };

  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  function showWarning(seconds) {
    if (!dialog) {
      dialog = el("dialog", "portal-dialog session-dialog");
      dialog.setAttribute("aria-labelledby", "session-title");
      const body = el("div", "dialog-body");
      const title = el("h2", "", "Are you still there?"); title.id = "session-title";
      countdown = el("p", "session-countdown"); countdown.setAttribute("role", "timer"); countdown.setAttribute("aria-live", "off");
      const note = el("p", "muted", "To protect client information, you are signed out after a while without activity. Your work on this screen is kept.");
      const actions = el("div", "dialog-actions");
      const stay = el("button", "button button-primary", "Stay signed in"); stay.type = "button";
      const out = el("button", "button button-quiet", "Sign out now"); out.type = "button";
      stay.addEventListener("click", () => { active(true); ping(); hideWarning(); });
      out.addEventListener("click", () => signOut("signed-out"));
      dialog.addEventListener("cancel", event => { event.preventDefault(); stay.click(); });
      actions.append(out, stay); body.append(title, countdown, note, actions); dialog.append(body); document.body.append(dialog);
    }
    const m = Math.floor(seconds / 60), s = String(Math.max(0, seconds % 60)).padStart(2, "0");
    countdown.textContent = `You will be signed out in ${m}:${s}.`;
    if (!dialog.open) { dialog.showModal(); dialog.querySelector(".button-primary").focus(); }
  }
  function hideWarning() { if (dialog && dialog.open) dialog.close(); }

  async function signOut(reason) {
    if (leaving) return;
    try { await send("/api/auth/logout", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: "{}"}); } catch (e) { /* the server ends the session by itself */ }
    signIn(reason);
  }
  async function ping() {
    try {
      const r = await send("/api/auth/me");
      if (r.status === 401) return signIn("expired");
      const d = await r.json();
      if (d.session && d.session.idle_seconds) idle = d.session.idle_seconds;
      lastServer = Date.now();
    } catch (e) { /* offline for a moment: the next tick tries again */ }
  }

  let wrote = 0;
  function active(force) {
    last = Date.now();
    if (dialog && dialog.open && !force) return;          // while the warning is up only its buttons count
    if (force || last - wrote > 5000) { wrote = last; try { localStorage.setItem(ACTIVE, String(last)); } catch (e) { /* private mode */ } }
  }
  ["pointerdown", "keydown", "wheel", "touchstart"].forEach(type => window.addEventListener(type, () => active(false), {passive: true, capture: true}));
  window.addEventListener("scroll", () => active(false), {passive: true, capture: true});

  function tick() {
    if (leaving) return;
    const quiet = (Date.now() - (dialog && dialog.open ? Math.max(wrote, seenShared()) : seen())) / 1000;
    if (quiet >= idle) return void signOut("idle");
    if (quiet >= idle - WARN) return showWarning(Math.ceil(idle - quiet));
    hideWarning();
    // somebody is working but nothing was sent for a while: tell the server, so its idle limit moves on too
    if (quiet < 90 && Date.now() - lastServer > Math.min(240, idle / 3) * 1000) ping();
  }
  function seenShared() { try { return Number(localStorage.getItem(ACTIVE)) || 0; } catch (e) { return 0; } }
  // "Preparing your workspace..." must not stay for ever when the server cannot be reached.
  setTimeout(() => {
    const loading = document.getElementById("loading");
    if (!loading || loading.hidden || leaving) return;
    const card = el("div", "load-problem"); card.setAttribute("role", "alert");
    const again = el("button", "button button-primary", "Try again"); again.type = "button"; again.addEventListener("click", () => location.reload());
    card.append(el("h2", "", "The workspace is taking too long to load"), el("p", "", "Check your internet connection. If it keeps happening, the server may be restarting: wait a minute and try again."), again);
    loading.replaceChildren(card);
  }, 15000);
  active(true);
  ping();
  setInterval(tick, 1000);
})();
