import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { createServer } from "node:http";
import { afterEach, test } from "node:test";
import { publishReleaseEvent } from "../scripts/publish-release-event.mjs";

const secret = "test-release-webhook-secret-with-at-least-32-characters";
const event = {
  service: "order-service",
  version: "v1.2.3",
  commit_sha: "ABCDEF0123456789",
  changed_files: ["src/orders.ts", "src/orders.ts"],
  deployed_at: "2026-09-27T10:00:00Z",
};
const servers = new Set();

afterEach(async () => {
  await Promise.all([...servers].map((server) => new Promise((resolve) => server.close(resolve))));
  servers.clear();
});

async function listen(handler) {
  const server = createServer(handler);
  servers.add(server);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return `http://127.0.0.1:${port}/internal/releases`;
}

test("sends a normalized payload signed over the exact raw body", async () => {
  const url = await listen((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const rawBody = Buffer.concat(chunks).toString("utf8");
      const signature = request.headers["x-release-signature"];
      const expected = `sha256=${createHmac("sha256", secret).update(rawBody).digest("hex")}`;
      assert.equal(signature, expected);
      const payload = JSON.parse(rawBody);
      assert.equal(payload.commit_sha, "abcdef0123456789");
      assert.deepEqual(payload.changed_files, ["src/orders.ts"]);
      assert.equal("credential" in payload, false);
      response.writeHead(202, { "content-type": "application/json" });
      response.end('{"accepted":true}');
    });
  });

  const result = await publishReleaseEvent({ url, secret, event: { ...event, credential: "must-not-send" } });
  assert.equal(result.status, 202);
  assert.equal(result.event.service, "order-service");
});

test("rejects remote plain HTTP before making a request", async () => {
  let requests = 0;
  await assert.rejects(
    publishReleaseEvent({
      url: "http://release.example.test/internal/releases",
      secret,
      event,
      fetchImpl: async () => { requests += 1; },
    }),
    /require HTTPS/,
  );
  assert.equal(requests, 0);
});

test("does not follow redirects that could forward the signature", async () => {
  const url = await listen((_request, response) => {
    response.writeHead(302, { location: "http://127.0.0.1:1/capture" });
    response.end();
  });
  await assert.rejects(publishReleaseEvent({ url, secret, event }));
});

test("rejects invalid metadata and non-success responses", async () => {
  let requests = 0;
  const fetchImpl = async () => {
    requests += 1;
    return { ok: false, status: 503 };
  };
  const url = "http://localhost/internal/releases";
  await assert.rejects(publishReleaseEvent({ url, secret, event: { ...event, changed_files: [] }, fetchImpl }), /changed_files/);
  await assert.rejects(publishReleaseEvent({
    url,
    secret,
    event: { ...event, changed_files: Array(34).fill("a".repeat(300)) },
    fetchImpl,
  }), /10,000-character/);
  assert.equal(requests, 0);
  await assert.rejects(publishReleaseEvent({ url, secret, event, fetchImpl }), /HTTP 503/);
  assert.equal(requests, 1);
});
