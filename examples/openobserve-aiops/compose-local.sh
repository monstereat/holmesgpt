#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/holmesgpt-aiops"
runtime_env="$config_dir/local-compose.env"
model_env="$config_dir/deepseek.env"

for env_file in "$runtime_env" "$model_env"; do
    if [[ ! -f "$env_file" || -L "$env_file" ]]; then
        echo "Required local configuration is missing or unsafe: $env_file" >&2
        exit 1
    fi
    python3 - "$env_file" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
metadata = os.stat(path, follow_symlinks=False)
if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
    raise SystemExit(f"Local configuration must be owned by this user and mode 0600: {path}")
PY
done

set -a
source "$runtime_env"
source "$model_env"
set +a

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    echo "DEEPSEEK_API_KEY is empty in $model_env" >&2
    exit 1
fi

exec docker compose -f "$script_dir/docker-compose.yaml" "$@"
