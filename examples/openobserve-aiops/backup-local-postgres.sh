#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <new-backup-file>" >&2
    exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose_script="$script_dir/compose-local.sh"
backup_file="$1"

if [[ -e "$backup_file" || -L "$backup_file" ]]; then
    echo "Refusing to overwrite an existing backup file" >&2
    exit 1
fi

umask 077
backup_dir="$(dirname -- "$backup_file")"
mkdir -p -- "$backup_dir"
temporary_file="$(mktemp "${backup_file}.tmp.XXXXXX")"
cleanup() {
    if [[ -n "${temporary_file:-}" ]]; then
        rm -f -- "$temporary_file"
    fi
}
trap cleanup EXIT

# Keep PostgreSQL private to the Compose network; pg_dump streams the archive
# from the database container to a protected host-side temporary file.
"$compose_script" exec -T postgres pg_dump \
    --format=custom --no-owner --no-password \
    --username=aiops --dbname=aiops > "$temporary_file"
"$compose_script" exec -T postgres pg_restore --list < "$temporary_file" >/dev/null

# Link within the same directory so publishing is atomic and refuses a path
# created after the initial existence check.
ln "$temporary_file" "$backup_file"
rm -- "$temporary_file"
temporary_file=""

echo "Created and verified local PostgreSQL backup: $backup_file"
