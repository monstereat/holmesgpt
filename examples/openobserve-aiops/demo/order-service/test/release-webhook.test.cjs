const assert = require("node:assert/strict");
const { createHmac } = require("node:crypto");
const { test } = require("node:test");

require("ts-node/register");
const { normalizeReleaseEvent, verifyReleaseSignature } = require("../src/release-webhook");

const secret = "demo-release-webhook-secret-with-more-than-32-characters";
const body = Buffer.from(JSON.stringify({
  service: "order-service",
  version: "v1.0.1",
  commit_sha: "ABCDEF0123456789",
  changed_files: ["src/orders.ts", "src/inventory.ts", "src/orders.ts"],
  deployed_at: "2026-09-25T08:00:00Z",
}));
const signature = `sha256=${createHmac("sha256", secret).update(body).digest("hex")}`;

test("accepts a correctly signed release payload", () => {
  assert.equal(verifyReleaseSignature(body, signature, secret), true);
});

test("rejects missing, malformed, weak-secret, and tampered signatures", () => {
  assert.equal(verifyReleaseSignature(undefined, signature, secret), false);
  assert.equal(verifyReleaseSignature(body, "bad", secret), false);
  assert.equal(verifyReleaseSignature(body, signature, "short"), false);
  assert.equal(verifyReleaseSignature(Buffer.from("tampered"), signature, secret), false);
});

test("normalizes a bounded release event and deduplicates changed paths", () => {
  const event = normalizeReleaseEvent(JSON.parse(body.toString("utf8")));

  assert.deepEqual(event, {
    service: "order-service",
    version: "v1.0.1",
    commit_sha: "abcdef0123456789",
    changed_files: ["src/orders.ts", "src/inventory.ts"],
    deployed_at: "2026-09-25T08:00:00.000Z",
  });
});

test("rejects incomplete or oversized release metadata", () => {
  const base = JSON.parse(body.toString("utf8"));
  assert.equal(normalizeReleaseEvent({ ...base, commit_sha: "not-a-sha" }), null);
  assert.equal(normalizeReleaseEvent({ ...base, changed_files: [] }), null);
  assert.equal(normalizeReleaseEvent({ ...base, changed_files: Array(101).fill("a.ts") }), null);
  assert.equal(normalizeReleaseEvent({ ...base, changed_files: ["src/\ninjected.ts"] }), null);
  assert.equal(normalizeReleaseEvent({ ...base, deployed_at: "yesterday" }), null);
  assert.equal(normalizeReleaseEvent({ ...base, deployed_at: "0" }), null);
});

test("ignores arbitrary fields from CI payloads", () => {
  const event = normalizeReleaseEvent({
    ...JSON.parse(body.toString("utf8")),
    actor_email: "operator@example.test",
    credential: "must-not-be-indexed",
  });

  assert.equal("actor_email" in event, false);
  assert.equal("credential" in event, false);
});
