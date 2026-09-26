#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rules_dir="$repo_dir/monitoring"
prometheus_image="prom/prometheus:v3.14.0"

docker run --rm --network none \
    --mount "type=bind,src=$rules_dir,dst=/rules,readonly" \
    --entrypoint promtool "$prometheus_image" \
    check rules /rules/aiops-alerts.yml

docker run --rm --network none \
    --mount "type=bind,src=$rules_dir,dst=/rules,readonly" \
    --entrypoint promtool "$prometheus_image" \
    test rules /rules/aiops-alerts.test.yml
