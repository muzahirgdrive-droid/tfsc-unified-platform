"use strict";
(() => {
  const result = document.getElementById("result");
  document.getElementById("file").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    result.textContent = "Checking…";
    try {
      const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const hex = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
      const response = await fetch(`/api/verify/${hex}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The check could not be completed.");
      result.textContent = data.match
        ? `Verified. "${data.document}" is the unchanged completed copy of envelope ${data.envelope} (${data.company}), completed ${new Date(data.completed_at * 1000).toLocaleString()}.`
        : "Not verified. This file does not match any completed document. It may have been changed after signing, or it was not completed through TFSC Agreements.";
    } catch (error) { result.textContent = error.message || "The check could not be completed."; }
  });
})();
