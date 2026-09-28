#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose_file="$script_dir/docker-compose.yaml"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/holmesgpt-aiops"
runtime_env="$config_dir/local-compose.env"

mkdir -p "$config_dir"
chmod 700 "$config_dir"

if [[ -e "$runtime_env" ]]; then
    echo "Local runtime config already exists; refusing to replace it." >&2
    exit 1
fi

if [[ -n "$(docker ps -aq --filter label=com.docker.compose.project=holmesgpt-aiops-goal)" ]] || \
   [[ -n "$(docker volume ls -q --filter label=com.docker.compose.project=holmesgpt-aiops-goal)" ]]; then
    echo "This Compose project already has containers or volumes; refusing to generate replacement credentials." >&2
    echo "Restore the existing local-compose.env before restarting the stack." >&2
    exit 1
fi

python3 - "$runtime_env" <<'PY'
import json
import os
import secrets
import shlex
import sys

path = sys.argv[1]
users = [
    {"username": "admin", "password": secrets.token_hex(24), "role": "admin", "resource_scopes": ["order-service"]},
    {"username": "operator", "password": secrets.token_hex(24), "role": "operator", "resource_scopes": ["order-service"]},
    {"username": "approver", "password": secrets.token_hex(24), "role": "approver", "resource_scopes": ["order-service"]},
]
values = {
    "AIOPS_API_HOST_PORT": "8081",
    "AIOPS_DB_MIGRATOR_PASSWORD": secrets.token_hex(32),
    "AIOPS_DB_RUNTIME_PASSWORD": secrets.token_hex(32),
    "AIOPS_DB_WORKER_PASSWORD": secrets.token_hex(32),
    "AIOPS_METRICS_TOKEN": secrets.token_hex(32),
    "AIOPS_TEST_USERS_JSON": json.dumps(users, separators=(",", ":")),
    "ALERT_WEBHOOK_TOKEN": secrets.token_hex(32),
    "CHAOS_MODE": "normal",
    "HOLMES_API_KEY": secrets.token_hex(32),
    "HOLMES_MODEL": "deepseek/deepseek-flash",
    "HOLMES_TIMEOUT_SECONDS": "900",
    "OPENOBSERVE_PROXY_PASSWORD": secrets.token_hex(32),
    "OPENOBSERVE_PROXY_USERNAME": "holmes-proxy",
    "ORDER_ACTION_TOKEN": secrets.token_hex(32),
    "POSTGRES_PASSWORD": secrets.token_hex(32),
    "RELEASE_VERSION": "local",
    "RELEASE_WEBHOOK_SECRET": secrets.token_hex(32),
    "SESSION_SIGNING_KEY": secrets.token_urlsafe(48),
    "ZO_ROOT_USER_EMAIL": "demo@example.test",
    "ZO_ROOT_USER_PASSWORD": secrets.token_hex(32),
}
content = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in sorted(values.items()))
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as runtime_file:
    runtime_file.write(content)
PY

echo "Created local runtime credentials at $runtime_env with mode 0600."
echo "The random demo account passwords are exposed only through the local workbench login helper."
echo "Set DEEPSEEK_API_KEY in $config_dir/deepseek.env before starting live investigations."
