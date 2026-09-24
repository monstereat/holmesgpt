const assert = require("node:assert/strict");
const { test } = require("node:test");
const monitor = require("../public/monitoring.js");

test("redacts credentials, email addresses, and secret query parameters", () => {
  const result = monitor.redact(
    "Bearer abc.def token=secret alice@example.com /orders?api_key=hidden",
    500,
  );
  assert.equal(
    result,
    "Bearer [REDACTED] token=[REDACTED] [EMAIL] /orders?api_key=[REDACTED]",
  );
});

test("sampling clamps invalid and out-of-range probabilities", () => {
  assert.equal(monitor.shouldSample(0, () => 0), false);
  assert.equal(monitor.shouldSample(1, () => 0.999), true);
  assert.equal(monitor.shouldSample(2, () => 0.999), true);
  assert.equal(monitor.shouldSample(-1, () => 0), false);
  assert.equal(monitor.shouldSample("invalid", () => 0.5), true);
});

test("traceparent uses valid random W3C ID fields", () => {
  const traceparent = monitor.newTraceparent();
  assert.match(traceparent, /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
  assert.equal(monitor.traceIdFrom(traceparent), traceparent.split("-")[1]);
});

test("fetch requests receive a traceparent header", async () => {
  let headers;
  const target = {
    DEMO_TELEMETRY_SAMPLE_RATE: 0,
    fetch: (_input, init) => {
      headers = new Headers(init.headers);
      return Promise.resolve({});
    },
    location: { origin: "http://demo.test", pathname: "/" },
    navigator: { userAgent: "demo" },
    addEventListener() {},
  };
  monitor.install(target);

  await target.fetch("/orders");
  assert.match(headers.get("traceparent"), /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
});

test("reported browser errors are sampled and sanitized before upload", async () => {
  const calls = [];
  const telemetry = monitor.install({
    DEMO_TELEMETRY_SAMPLE_RATE: 1,
    fetch: (input, init) => {
      calls.push({ input, init });
      return Promise.resolve({});
    },
    location: { origin: "http://demo.test" },
    navigator: { userAgent: "demo alice@example.com" },
    addEventListener() {},
  });

  telemetry.report({
    message: "request failed token=private",
    route: "/orders?password=hunter2",
    trace_id: "a".repeat(32),
  });
  await Promise.resolve();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].input, "/internal/frontend-errors");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    message: "request failed token=[REDACTED]",
    stack: "",
    route: "/orders",
    trace_id: "a".repeat(32),
    user_agent: "demo [EMAIL]",
  });
});

test("sample rate zero drops browser error reports", () => {
  let calls = 0;
  const telemetry = monitor.install({
    DEMO_TELEMETRY_SAMPLE_RATE: 0,
    fetch: () => {
      calls += 1;
      return Promise.resolve({});
    },
    location: { origin: "http://demo.test" },
    navigator: { userAgent: "demo" },
    addEventListener() {},
  });

  telemetry.report({ message: "not sampled", route: "/orders" });
  assert.equal(calls, 0);
});
