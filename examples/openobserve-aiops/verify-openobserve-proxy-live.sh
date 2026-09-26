#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENOBSERVE_IMAGE="${OPENOBSERVE_TEST_IMAGE:-public.ecr.aws/zinclabs/openobserve:v1.0.3}"
TEST_ID="$$-$(date +%s)"
NETWORK="aiops-proxy-check-${TEST_ID}"
OPENOBSERVE="aiops-proxy-check-oo-${TEST_ID}"
PROXY="aiops-proxy-check-proxy-${TEST_ID}"
PROXY_IMAGE="aiops-openobserve-proxy:live-check"
DOCKER_SUBNET="${AIOPS_TEST_DOCKER_SUBNET:-10.250.254.0/29}"
OPENOBSERVE_TEST_PASSWORD="AioPsTest-$(openssl rand -hex 16)9!"
PROXY_TEST_PASSWORD="ProxyTest-$(openssl rand -hex 16)8!"

cleanup() {
  docker rm -f "$PROXY" "$OPENOBSERVE" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --internal --subnet "$DOCKER_SUBNET" "$NETWORK" >/dev/null
docker build -t "$PROXY_IMAGE" "$SCRIPT_DIR/openobserve-proxy"

docker run -d \
  --name "$OPENOBSERVE" \
  --network "$NETWORK" \
  --network-alias openobserve \
  --tmpfs /data:rw,noexec,nosuid,size=512m \
  -e ZO_ROOT_USER_EMAIL=proxy-check@example.test \
  -e ZO_ROOT_USER_PASSWORD="$OPENOBSERVE_TEST_PASSWORD" \
  -e ZO_DATA_DIR=/data \
  -e ZO_HTTP_PORT=5080 \
  "$OPENOBSERVE_IMAGE" >/dev/null

docker run -d \
  --name "$PROXY" \
  --network "$NETWORK" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -e AIOPS_ENV=local \
  -e OPENOBSERVE_PROXY_USERNAME=holmes-test \
  -e OPENOBSERVE_PROXY_PASSWORD="$PROXY_TEST_PASSWORD" \
  -e OPENOBSERVE_UPSTREAM_URL=http://openobserve:5080 \
  -e OPENOBSERVE_ORG=default \
  -e OPENOBSERVE_UPSTREAM_USERNAME=proxy-check@example.test \
  -e OPENOBSERVE_UPSTREAM_PASSWORD="$OPENOBSERVE_TEST_PASSWORD" \
  -e 'OPENOBSERVE_ALLOWED_FIELDS_JSON={"app_logs":["_timestamp","trace_id","service","message","evaluation_run_id","evaluation_case_id"],"frontend_errors":["_timestamp","trace_id","service","message"]}' \
  "$PROXY_IMAGE" >/dev/null

docker exec -i "$PROXY" python - <<'PY'
import base64
import json
import os
import time
import uuid
import urllib.error
import urllib.request


def request(url, method="GET", body=None, username=None, password=None):
    headers = {"Accept": "application/json"}
    data = None
    if username is not None and password is not None:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    request_value = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request_value, timeout=10) as response:
            return response.status, response.read(1_000_000)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1_000_000)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def wait_for(url, username=None, password=None):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status, _ = request(url, username=username, password=password)
        if status == 200:
            return
        time.sleep(1)
    raise SystemExit("service readiness timed out")


upstream_user = os.environ["OPENOBSERVE_UPSTREAM_USERNAME"]
upstream_password = os.environ["OPENOBSERVE_UPSTREAM_PASSWORD"]
proxy_user = os.environ["OPENOBSERVE_PROXY_USERNAME"]
proxy_password = os.environ["OPENOBSERVE_PROXY_PASSWORD"]
wait_for("http://openobserve:5080/healthz", upstream_user, upstream_password)
wait_for("http://127.0.0.1:8090/healthz")

timestamp = time.time_ns() // 1_000
trace_id = "a" * 32
run_id = uuid.uuid4().hex
case_id = "proxy-field-policy-check"
record = {
    "_timestamp": timestamp,
    "trace_id": trace_id,
    "service": "proxy-live-check",
    "message": "synthetic-policy-marker",
    "evaluation_run_id": run_id,
    "evaluation_case_id": case_id,
    "private_probe": "must-not-be-returned",
}
status, _ = request(
    "http://openobserve:5080/api/default/app_logs/_json",
    "POST",
    [record],
    upstream_user,
    upstream_password,
)
if status != 200:
    raise SystemExit(f"synthetic OpenObserve ingest failed: HTTP {status}")

streams_url = "http://127.0.0.1:8090/api/default/streams?fetchSchema=true&type=logs"
deadline = time.monotonic() + 30
while True:
    status, raw = request(streams_url, username=proxy_user, password=proxy_password)
    if status == 200:
        streams = json.loads(raw).get("list", [])
        app_logs = next((item for item in streams if item.get("name") == "app_logs"), None)
        if app_logs is not None:
            break
    if time.monotonic() >= deadline:
        raise SystemExit("OpenObserve stream schema did not become visible through proxy")
    time.sleep(1)

if any(item.get("name") not in {"app_logs", "frontend_errors"} for item in streams):
    raise SystemExit("proxy returned a stream outside its allowlist")
schema = app_logs.get("schema", [])
schema_names = {
    item if isinstance(item, str) else item.get("name")
    for item in schema
    if isinstance(item, (str, dict))
}
if "private_probe" in schema_names:
    raise SystemExit("proxy returned a field outside its schema allowlist")

search_url = "http://127.0.0.1:8090/api/default/_search"
query = {
    "query": {
        "sql": (
            f"SELECT * FROM app_logs WHERE trace_id = '{trace_id}' "
            f"AND evaluation_run_id = '{run_id}' AND evaluation_case_id = '{case_id}'"
        ),
        "start_time": timestamp - 30_000_000,
        "end_time": timestamp + 30_000_000,
        "from": 0,
        "size": 10,
    },
    "search_type": "ui",
    "timeout": 5,
}
deadline = time.monotonic() + 30
while True:
    status, raw = request(search_url, "POST", query, proxy_user, proxy_password)
    if status == 200 and b"synthetic-policy-marker" in raw:
        break
    if status not in {0, 200}:
        raise SystemExit(f"allowlisted OpenObserve search failed: HTTP {status}")
    if time.monotonic() >= deadline:
        raise SystemExit("synthetic log did not become searchable through proxy")
    time.sleep(1)
if b"private_probe" in raw or b"must-not-be-returned" in raw:
    raise SystemExit("proxy returned an unallowlisted field or value")

query["query"]["sql"] = f"SELECT private_probe FROM app_logs WHERE trace_id = '{trace_id}'"
status, _ = request(search_url, "POST", query, proxy_user, proxy_password)
if status != 400:
    raise SystemExit(f"proxy did not reject an unallowlisted field: HTTP {status}")

print("PASS: OpenObserve ingest/search compatibility, schema/row filtering, and unallowlisted-field rejection")
PY
