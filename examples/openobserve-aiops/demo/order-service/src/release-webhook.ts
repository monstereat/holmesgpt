import { createHmac, timingSafeEqual } from "node:crypto";

export type ReleaseEvent = {
  service: string;
  version: string;
  commit_sha: string;
  changed_files: string[];
  deployed_at: string;
};

const SERVICE_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$/;
const VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$/;
const COMMIT_RE = /^[a-fA-F0-9]{7,40}$/;
const ISO_DATETIME_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/;

export function verifyReleaseSignature(
  rawBody: Buffer | undefined,
  signature: string | undefined,
  secret: string,
): boolean {
  if (!rawBody || secret.length < 32 || !signature) return false;
  const match = /^sha256=([a-fA-F0-9]{64})$/.exec(signature);
  if (!match) return false;

  const expected = createHmac("sha256", secret).update(rawBody).digest();
  const supplied = Buffer.from(match[1], "hex");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

export function normalizeReleaseEvent(payload: unknown): ReleaseEvent | null {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const event = payload as Record<string, unknown>;
  if (
    typeof event.service !== "string" || !SERVICE_RE.test(event.service)
    || typeof event.version !== "string" || !VERSION_RE.test(event.version)
    || typeof event.commit_sha !== "string" || !COMMIT_RE.test(event.commit_sha)
    || typeof event.deployed_at !== "string" || event.deployed_at.length > 40
    || !ISO_DATETIME_RE.test(event.deployed_at)
    || !Array.isArray(event.changed_files) || event.changed_files.length < 1
    || event.changed_files.length > 100
  ) {
    return null;
  }

  const deployedAt = new Date(event.deployed_at);
  if (Number.isNaN(deployedAt.getTime())) return null;

  const changedFiles: string[] = [];
  let totalPathLength = 0;
  for (const file of event.changed_files) {
    if (
      typeof file !== "string" || file.length < 1 || file.length > 300
      || /[\u0000-\u001f\u007f]/.test(file)
    ) {
      return null;
    }
    totalPathLength += file.length;
    if (totalPathLength > 10_000) return null;
    if (!changedFiles.includes(file)) changedFiles.push(file);
  }

  return {
    service: event.service,
    version: event.version,
    commit_sha: event.commit_sha.toLowerCase(),
    changed_files: changedFiles,
    deployed_at: deployedAt.toISOString(),
  };
}
