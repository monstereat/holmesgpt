#!/usr/bin/env node

import { createHmac } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const servicePattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$/;
const versionPattern = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$/;
const commitPattern = /^[a-fA-F0-9]{7,40}$/;
const timestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/;
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);

function validateTarget(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error("AIOPS_RELEASE_WEBHOOK_URL must be an absolute HTTP(S) URL");
  }
  if (!new Set(["http:", "https:"]).has(url.protocol)) {
    throw new Error("AIOPS_RELEASE_WEBHOOK_URL must use HTTP or HTTPS");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("AIOPS_RELEASE_WEBHOOK_URL cannot contain credentials, a query, or a fragment");
  }
  if (url.protocol !== "https:" && !loopbackHosts.has(url.hostname)) {
    throw new Error("Release webhook requests require HTTPS except for loopback testing");
  }
  return url;
}

function validateEvent(event) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    throw new Error("Release event must be an object");
  }
  const { service, version, commit_sha: commitSha, changed_files: changedFiles, deployed_at: deployedAt } = event;
  if (typeof service !== "string" || !servicePattern.test(service)) throw new Error("Release service is invalid");
  if (typeof version !== "string" || !versionPattern.test(version)) throw new Error("Release version is invalid");
  if (typeof commitSha !== "string" || !commitPattern.test(commitSha)) throw new Error("Release commit SHA is invalid");
  if (!Array.isArray(changedFiles) || changedFiles.length < 1 || changedFiles.length > 100
    || changedFiles.some((file) => typeof file !== "string" || file.length < 1 || file.length > 300
      || /[\u0000-\u001f\u007f]/.test(file))) {
    throw new Error("Release changed_files must contain 1 to 100 valid paths");
  }
  if (changedFiles.reduce((total, file) => total + file.length, 0) > 10_000) {
    throw new Error("Release changed_files exceeds the 10,000-character limit");
  }
  if (typeof deployedAt !== "string" || deployedAt.length > 40 || !timestampPattern.test(deployedAt)
    || Number.isNaN(Date.parse(deployedAt))) {
    throw new Error("Release deployed_at must be an ISO timestamp with a timezone");
  }
  return {
    service,
    version,
    commit_sha: commitSha.toLowerCase(),
    changed_files: [...new Set(changedFiles)],
    deployed_at: new Date(deployedAt).toISOString(),
  };
}

export async function publishReleaseEvent({ url, secret, event, fetchImpl = fetch, timeoutMs = 5000 }) {
  const target = validateTarget(url);
  if (typeof secret !== "string" || secret.length < 32) {
    throw new Error("RELEASE_WEBHOOK_SECRET must contain at least 32 characters");
  }
  const payload = validateEvent(event);
  const rawBody = JSON.stringify(payload);
  const signature = `sha256=${createHmac("sha256", secret).update(rawBody).digest("hex")}`;
  const response = await fetchImpl(target, {
    method: "POST",
    redirect: "error",
    signal: AbortSignal.timeout(timeoutMs),
    headers: {
      "content-type": "application/json",
      "x-release-signature": signature,
    },
    body: rawBody,
  });
  if (!response.ok) throw new Error(`Release webhook returned HTTP ${response.status}`);
  return { status: response.status, event: payload };
}

async function main() {
  const rawChangedFiles = process.env.RELEASE_CHANGED_FILES_JSON;
  let changedFiles;
  try {
    changedFiles = JSON.parse(rawChangedFiles || "");
  } catch {
    throw new Error("RELEASE_CHANGED_FILES_JSON must be a JSON array");
  }
  const commitSha = process.env.RELEASE_COMMIT_SHA || process.env.GITHUB_SHA || process.env.CI_COMMIT_SHA;
  const event = {
    service: process.env.RELEASE_SERVICE || "order-service",
    version: process.env.RELEASE_VERSION,
    commit_sha: commitSha,
    changed_files: changedFiles,
    deployed_at: process.env.RELEASE_DEPLOYED_AT || new Date().toISOString(),
  };
  const result = await publishReleaseEvent({
    url: process.env.AIOPS_RELEASE_WEBHOOK_URL,
    secret: process.env.RELEASE_WEBHOOK_SECRET,
    event,
  });
  console.log(`Release event accepted (HTTP ${result.status}): ${result.event.service}@${result.event.version}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
