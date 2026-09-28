#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose_script="$script_dir/compose-local.sh"

compose_local() {
    "$compose_script" "$@"
}

compose_local ps --format '{{json .}}' | python3 -c '
import json
import sys

required_health = {
    "holmes-api": "healthy",
    "incident-api": "healthy",
    "incident-worker": "healthy",
    "local-ingress": "healthy",
    "openobserve": None,
    "openobserve-proxy": "healthy",
    "order-service": "healthy",
    "otel-collector": None,
    "postgres": "healthy",
    "redis": "healthy",
}
services = {}
for line in sys.stdin:
    if line.strip():
        service = json.loads(line)
        services[service.get("Service")] = service

failures = []
for name, health in required_health.items():
    service = services.get(name)
    if service is None:
        failures.append(f"{name}: missing")
        continue
    state = service.get("State")
    reported_health = service.get("Health")
    if state != "running":
        failures.append(f"{name}: state={state}")
    if health and reported_health != health:
        failures.append(f"{name}: health={reported_health}")

if failures:
    print("Local AIOps services are not ready:", file=sys.stderr)
    print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
    raise SystemExit(1)
print(f"Compose service state passed ({len(required_health)} required services).")
'

local_url() {
    local service="$1"
    local container_port="$2"
    local published
    published="$(compose_local port "$service" "$container_port" | head -n 1)"
    if [[ ! "$published" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
        echo "$service port $container_port is not bound to loopback: $published" >&2
        return 1
    fi
    printf 'http://%s' "$published"
}

check_json_status() {
    local name="$1"
    local url="$2"
    local expected="$3"
    local response
    local actual
    response="$(curl --fail --silent --show-error --connect-timeout 3 --max-time 5 "$url")"
    actual="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))')"
    if [[ "$actual" != "$expected" ]]; then
        echo "$name returned status '$actual', expected '$expected'" >&2
        return 1
    fi
    echo "$name: $actual"
}

holmes_url="$(local_url holmes-api 5050)"
incident_url="$(local_url local-ingress 8081)"
openobserve_url="$(local_url openobserve 5080)"
order_url="$(local_url local-ingress 8080)"

check_json_status "Holmes readiness" "$holmes_url/readyz" "ready"
check_json_status "Incident API readiness" "$incident_url/readyz" "ready"
check_json_status "OpenObserve health" "$openobserve_url/healthz" "ok"

order_status="$(curl --silent --show-error --connect-timeout 3 --max-time 5 --output /dev/null --write-out '%{http_code}' "$order_url/")"
if [[ "$order_status" != "200" ]]; then
    echo "Order service returned HTTP $order_status, expected HTTP 200" >&2
    exit 1
fi
echo "Order service: HTTP $order_status"

compose_local exec -T incident-worker python -c '
import os
from holmes_client import HolmesClient

HolmesClient(os.environ["HOLMES_API_URL"], os.environ["HOLMES_API_KEY"], timeout_seconds=10).check_openobserve_toolset()
print("Authenticated Holmes OpenObserve toolset gate: enabled")
'

echo "Local service readiness passed. No model request or business-data write was performed."
