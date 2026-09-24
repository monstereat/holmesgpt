/* Small demo-only browser monitor. Do not treat this as a production SDK. */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.HolmesMonitor = api;
  if (root.document) root.HolmesTelemetry = api.install(root);
})(globalThis, function (root) {
  const TRACEPARENT = /^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/i;
  const SECRET = /\b(password|token|api[_-]?key|secret|authorization)\b(\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;&]+)/gi;
  const QUERY_SECRET = /([?&](?:password|token|api[_-]?key|secret)=)[^&#\s]*/gi;
  const EMAIL = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;

  function redact(value, limit) {
    return String(value || "")
      .slice(0, limit)
      .replace(/\bBearer\s+[A-Z0-9._~+\/-]+=*/gi, "Bearer [REDACTED]")
      .replace(SECRET, "$1$2[REDACTED]")
      .replace(QUERY_SECRET, "$1[REDACTED]")
      .replace(EMAIL, "[EMAIL]");
  }

  function shouldSample(rate, random) {
    const probability = Number.isFinite(Number(rate))
      ? Math.max(0, Math.min(1, Number(rate)))
      : 1;
    return random() < probability;
  }

  function traceIdFrom(value) {
    const match = TRACEPARENT.exec(value || "");
    return match ? match[1].toLowerCase() : "";
  }

  function newHexId(bytes) {
    const values = new Uint8Array(bytes);
    root.crypto.getRandomValues(values);
    return Array.from(values, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function newTraceparent() {
    return `00-${newHexId(16)}-${newHexId(8)}-01`;
  }

  function safeRoute(value, origin) {
    try {
      return new URL(String(value || "/"), origin).pathname.slice(0, 200);
    } catch (_) {
      return "/";
    }
  }

  function install(target) {
    const originalFetch = target.fetch.bind(target);
    const sampleRate = target.DEMO_TELEMETRY_SAMPLE_RATE ?? 1;

    function report(payload) {
      if (!shouldSample(sampleRate, Math.random)) return;
      const safePayload = {
        message: redact(payload.message, 500),
        stack: redact(payload.stack, 1200),
        route: safeRoute(payload.route, target.location.origin),
        trace_id: /^[0-9a-f]{32}$/i.test(payload.trace_id || "")
          ? payload.trace_id.toLowerCase()
          : "",
        user_agent: redact(target.navigator.userAgent, 160),
      };
      void originalFetch("/internal/frontend-errors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        body: JSON.stringify(safePayload),
      }).catch(() => {});
    }

    target.fetch = function (input, init) {
      const url = typeof input === "string" ? input : input.url;
      if (url && url.startsWith("/internal/frontend-errors")) {
        return originalFetch(input, init);
      }

      const options = init || {};
      options.headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
      if (!options.headers.has("traceparent")) {
        options.headers.set("traceparent", newTraceparent());
      }
      const traceId = traceIdFrom(options.headers.get("traceparent"));
      return originalFetch(input, options).catch((error) => {
        report({ message: `fetch failed: ${error.message}`, route: url, trace_id: traceId });
        throw error;
      });
    };

    target.addEventListener("error", (event) => {
      report({ message: event.message, stack: event.error && event.error.stack, route: target.location.pathname });
    });
    target.addEventListener("unhandledrejection", (event) => {
      report({ message: `unhandledrejection: ${event.reason}`, route: target.location.pathname });
    });

    return { report };
  }

  return { install, newTraceparent, redact, shouldSample, traceIdFrom };
});
