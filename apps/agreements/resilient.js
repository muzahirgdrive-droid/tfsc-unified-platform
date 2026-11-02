"use strict";
// A restart for an update takes a few seconds. A request that never reached the application (no connection, or the
// proxy answered 502/503 without the application's JSON) is sent again for up to about 25 seconds instead of failing,
// so nobody loses what they were doing. Answers from the application itself are never repeated.
(() => {
  const send = window.fetch.bind(window);
  const waits = [1000, 2000, 3000, 4000, 5000, 5000, 5000];
  const fromProxy = r => (r.status === 502 || r.status === 503) && !(r.headers.get("content-type") || "").includes("application/json");
  window.fetch = async (input, init = {}) => {
    if (typeof input !== "string" || new URL(input, location.href).origin !== location.origin) return send(input, init);
    for (let i = 0; ; i++) {
      try {
        const response = await send(input, init);
        if (!fromProxy(response) || i >= waits.length) return response;
      } catch (e) {
        if (e.name === "AbortError" || i >= waits.length) throw e;
      }
      if (init.signal && init.signal.aborted) throw new DOMException("The request was cancelled.", "AbortError");
      await new Promise(done => setTimeout(done, waits[i]));
    }
  };
})();
