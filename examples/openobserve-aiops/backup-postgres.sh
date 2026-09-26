#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <new-backup-file>" >&2
    exit 2
fi

backup_file="$1"
if [[ -e "$backup_file" ]]; then
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

pg_dump --format=custom --no-owner --file="$temporary_file"
pg_restore --list "$temporary_file" >/dev/null

# Link within the same directory so publishing is atomic and fails if another
# process created the requested destination after the initial existence check.
ln "$temporary_file" "$backup_file"
rm -- "$temporary_file"
temporary_file=""

echo "Created and verified PostgreSQL backup: $backup_file"
