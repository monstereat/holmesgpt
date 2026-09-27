#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
git_root="$(git -C "$repo_dir/../.." rev-parse --show-toplevel)"
compose_project="${AIOPS_COMPOSE_PROJECT:-$(basename "$repo_dir")}"
node_bin="${NODE_BIN:-node}"

if [[ -z "${ZO_ROOT_USER_EMAIL:-}" || -z "${ZO_ROOT_USER_PASSWORD:-}" ]]; then
    echo "source the local test runtime environment before running this check" >&2
    exit 2
fi

host_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
secret="$(openssl rand -hex 32)"
export RELEASE_WEBHOOK_SECRET="$secret"
version="release-e2e-$(date -u +%Y%m%dT%H%M%S)-$$"
commit_sha="$(git -C "$git_root" rev-parse HEAD)"
deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
container_id=""

cleanup() {
    if [[ -n "$container_id" ]]; then
        docker stop "$container_id" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

container_id="$(docker compose --project-name "$compose_project" -f "$repo_dir/docker-compose.yaml" \
    run --no-deps --rm --detach --publish "127.0.0.1:${host_port}:8080" \
    --env RELEASE_WEBHOOK_SECRET order-service)"

ready=false
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:${host_port}/" 2>/dev/null; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo "temporary local order-service did not become ready" >&2
    exit 1
fi

AIOPS_RELEASE_WEBHOOK_URL="http://127.0.0.1:${host_port}/internal/releases" \
RELEASE_WEBHOOK_SECRET="$secret" \
RELEASE_VERSION="$version" \
RELEASE_COMMIT_SHA="$commit_sha" \
RELEASE_CHANGED_FILES_JSON='["examples/openobserve-aiops/demo/order-service/src/main.ts"]' \
RELEASE_DEPLOYED_AT="$deployed_at" \
RELEASE_SERVICE=order-service \
"$node_bin" "$repo_dir/demo/order-service/scripts/publish-release-event.mjs"

flush_status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
    --request POST "http://127.0.0.1:${host_port}/internal/flush")"
if [[ ! "$flush_status" =~ ^2[0-9][0-9]$ ]]; then
    echo "temporary local order-service flush returned HTTP $flush_status" >&2
    exit 1
fi

ZO_RELEASE_VERSION="$version" ZO_RELEASE_COMMIT="$commit_sha" \
    ZO_RELEASE_HOST_PORT=5080 \
    python3 - <<'PY'
import base64
import json
import os
import time
import urllib.error
import urllib.request

version = os.environ["ZO_RELEASE_VERSION"]
commit = os.environ["ZO_RELEASE_COMMIT"]
host_port = os.environ["ZO_RELEASE_HOST_PORT"]
username = os.environ["ZO_ROOT_USER_EMAIL"]
password = os.environ["ZO_ROOT_USER_PASSWORD"]
now_us = time.time_ns() // 1_000
body = {
    "query": {
        "sql": (
            "SELECT event_type FROM app_logs "
            "WHERE event_type = 'release_deployed' "
            f"AND release = '{version}' AND commit_sha = '{commit}'"
        ),
        "start_time": now_us - 120_000_000,
        "end_time": now_us + 60_000_000,
        "from": 0,
        "size": 10,
    },
    "search_type": "ui",
    "timeout": 5,
}
authorization = base64.b64encode(f"{username}:{password}".encode()).decode()
request = urllib.request.Request(
    f"http://127.0.0.1:{host_port}/api/default/_search",
    data=json.dumps(body).encode(),
    headers={
        "Authorization": f"Basic {authorization}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
    method="POST",
)
deadline = time.monotonic() + 30
while True:
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read(65_537))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"OpenObserve release-event search returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        result = None
    if result is not None:
        matches = result.get("total")
        if matches == 1:
            print("Release event end-to-end verification passed: signature accepted and exact event persisted")
            break
        if isinstance(matches, int) and matches > 1:
            raise SystemExit(f"OpenObserve returned {matches} matching release events; expected exactly one")
    if time.monotonic() >= deadline:
        raise SystemExit("OpenObserve did not make the exact release event searchable within 30 seconds")
    time.sleep(1)
PY
