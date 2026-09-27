#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 <existing-backup-file> <new-encrypted-file> <recipient-certificate>" >&2
    exit 2
fi

source_file="$1"
encrypted_file="$2"
recipient_certificate="$3"

if [[ ! -f "$source_file" || ! -r "$source_file" ]]; then
    echo "PostgreSQL backup file must exist and be readable" >&2
    exit 1
fi
if [[ ! -f "$recipient_certificate" || ! -r "$recipient_certificate" ]]; then
    echo "Recipient certificate must exist and be readable" >&2
    exit 1
fi
if [[ -e "$encrypted_file" ]]; then
    echo "Refusing to overwrite an existing encrypted backup" >&2
    exit 1
fi

openssl x509 -in "$recipient_certificate" -checkend 0 -noout >/dev/null

umask 077
encrypted_dir="$(dirname -- "$encrypted_file")"
mkdir -p -- "$encrypted_dir"
temporary_file="$(mktemp "${encrypted_file}.tmp.XXXXXX")"
cleanup() {
    if [[ -n "${temporary_file:-}" ]]; then
        rm -f -- "$temporary_file"
    fi
}
trap cleanup EXIT

openssl cms -encrypt -binary -aes-256-gcm \
    -in "$source_file" \
    -outform DER \
    -out "$temporary_file" \
    "$recipient_certificate"
openssl cms -cmsout -inform DER -in "$temporary_file" -noout

# Link within the destination directory so publishing is atomic and fails if
# another process created the requested path after the initial existence check.
ln "$temporary_file" "$encrypted_file"
rm -- "$temporary_file"
temporary_file=""

echo "Created encrypted PostgreSQL backup: $encrypted_file"
