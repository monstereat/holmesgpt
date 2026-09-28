#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "Usage: $0 <encrypted-backup-file> <new-plaintext-file> <recipient-certificate> <private-key>" >&2
    exit 2
fi

encrypted_file="$1"
plaintext_file="$2"
recipient_certificate="$3"
private_key="$4"

if [[ ! -f "$encrypted_file" || ! -r "$encrypted_file" ]]; then
    echo "Encrypted PostgreSQL backup must exist and be readable" >&2
    exit 1
fi
if [[ ! -f "$recipient_certificate" || ! -r "$recipient_certificate" ]]; then
    echo "Recipient certificate must exist and be readable" >&2
    exit 1
fi
if [[ ! -f "$private_key" || ! -r "$private_key" ]]; then
    echo "Recipient private key must exist and be readable" >&2
    exit 1
fi
if [[ -e "$plaintext_file" || -L "$plaintext_file" ]]; then
    echo "Refusing to overwrite an existing plaintext backup" >&2
    exit 1
fi

openssl x509 -in "$recipient_certificate" -noout >/dev/null

umask 077
plaintext_dir="$(dirname -- "$plaintext_file")"
mkdir -p -- "$plaintext_dir"
temporary_file="$(mktemp "${plaintext_file}.tmp.XXXXXX")"
cleanup() {
    if [[ -n "${temporary_file:-}" ]]; then
        rm -f -- "$temporary_file"
    fi
}
trap cleanup EXIT

openssl cms -decrypt -binary -inform DER \
    -in "$encrypted_file" \
    -recip "$recipient_certificate" \
    -inkey "$private_key" \
    -out "$temporary_file"
pg_restore --list "$temporary_file" >/dev/null

# Publish atomically without overwriting a path created after the initial check.
ln "$temporary_file" "$plaintext_file"
rm -- "$temporary_file"
temporary_file=""

echo "Created and verified mode-0600 plaintext PostgreSQL backup: $plaintext_file"
