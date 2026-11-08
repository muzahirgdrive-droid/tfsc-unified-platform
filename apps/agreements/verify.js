"use strict";
(() => {
  const result = document.getElementById("result");
  fetch("/api/public/workspace").then(r => r.json()).then(w => {
    document.getElementById("footer-copyright").textContent = `© ${w.year} ${w.copyright_holder}. All rights reserved.`;
    const help = document.getElementById("footer-help-email");
    if (w.help_email) { help.textContent = w.help_email; help.href = `mailto:${w.help_email}`; }
  }).catch(() => {});
  // The result: a green "Verified" box or a red "Not verified" one (classes only: the page's security policy allows no inline styles).
  const show = (state, title, text) => {
    result.className = "vf-result" + (state ? " " + state : "");
    const mark = document.createElement("span"); mark.className = "vf-mark"; mark.setAttribute("aria-hidden", "true");
    mark.textContent = state === "ok" ? "\u2713" : state === "bad" ? "!" : "\u2026";
    const body = document.createElement("div"); const b = document.createElement("b"); b.textContent = title; body.append(b, document.createTextNode(text));
    result.replaceChildren(mark, body);
  };
  document.getElementById("file").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    document.getElementById("file-name").textContent = file.name;
    show("", "Checking\u2026", " Calculating the fingerprint on your computer.");
    try {
      const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const hex = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
      const response = await fetch(`/api/verify/${hex}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The check could not be completed.");
      if (data.match) show("ok", "Verified.", ` "${data.document}" is the unchanged completed copy of envelope ${data.envelope} (${data.company}), completed ${new Date(data.completed_at * 1000).toLocaleString()}.`);
      else show("bad", "Not verified.", " This file does not match any completed document. It may have been changed after signing, or it was not completed through TFSC Agreements.");
    } catch (error) { show("bad", "The check could not be completed.", " " + (error.message || "Please try again.")); }
  });
})();
