#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
client_image="${AIOPS_POSTGRES_TLS_CLIENT_IMAGE:-holmesgpt-aiops-goal-incident-test:latest}"
postgres_image="postgres:16.6-alpine"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/aiops-postgres-tls.XXXXXX")"
postgres_container="aiops-pg-tls-${temporary_dir##*.}"
test_network="${AIOPS_POSTGRES_TLS_TEST_NETWORK:-}"
stage="locating internal test network"

cleanup() {
    local exit_status=$?
    trap - EXIT
    if [[ "$exit_status" -ne 0 ]]; then
        echo "::error title=PostgreSQL TLS verifier::Failed during ${stage} (exit ${exit_status})."
    fi
    docker rm -f "$postgres_container" >/dev/null 2>&1 || true
    rm -rf "$temporary_dir"
    exit "$exit_status"
}
trap cleanup EXIT

if [[ -z "$test_network" ]]; then
    while IFS= read -r container_id; do
        while IFS= read -r candidate; do
            [[ -n "$candidate" ]] || continue
            if [[ "$candidate" == *_test-isolated ]] && [[ "$(docker network inspect "$candidate" --format '{{.Internal}}')" == true ]]; then
                test_network="$candidate"
                break 2
            fi
        done < <(docker inspect "$container_id" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}')
    done < <(docker ps --quiet --filter label=com.docker.compose.service=postgres-test)
fi
if [[ -z "$test_network" ]]; then
    echo "Start the Compose postgres-test service or set AIOPS_POSTGRES_TLS_TEST_NETWORK to its internal test network." >&2
    exit 1
fi
if [[ "$(docker network inspect "$test_network" --format '{{.Internal}}')" != true ]]; then
    echo "PostgreSQL TLS verification requires an internal Docker test network: $test_network" >&2
    exit 1
fi

stage="building current incident test image"
docker build --file "$script_dir/alert-trigger/Dockerfile" --target test --tag "$client_image" "$script_dir/alert-trigger"

stage="generating temporary certificate authority and server certificate"
umask 077
openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
    -keyout "$temporary_dir/ca.key" -out "$temporary_dir/ca.crt" \
    -subj "/CN=AIOps PostgreSQL TLS Test CA" >/dev/null 2>&1
openssl req -new -newkey rsa:2048 -nodes \
    -keyout "$temporary_dir/server.key" -out "$temporary_dir/server.csr" \
    -subj "/CN=postgres-tls.test" \
    -addext "subjectAltName=DNS:postgres-tls.test" >/dev/null 2>&1
cat >"$temporary_dir/server.ext" <<'EOF'
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:postgres-tls.test
EOF
openssl x509 -req -in "$temporary_dir/server.csr" \
    -CA "$temporary_dir/ca.crt" -CAkey "$temporary_dir/ca.key" \
    -CAcreateserial -out "$temporary_dir/server.crt" -days 2 -sha256 \
    -extfile "$temporary_dir/server.ext" >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
    -keyout "$temporary_dir/wrong-ca.key" -out "$temporary_dir/wrong-ca.crt" \
    -subj "/CN=Wrong AIOps PostgreSQL TLS Test CA" >/dev/null 2>&1
trust_dir="$temporary_dir/trust"
mkdir -m 0755 "$trust_dir"
cp "$temporary_dir/ca.crt" "$trust_dir/ca.crt"
cp "$temporary_dir/wrong-ca.crt" "$trust_dir/wrong-ca.crt"
chmod 0644 "$trust_dir/ca.crt" "$trust_dir/wrong-ca.crt"
database_password="$(openssl rand -hex 32)"

stage="starting temporary PostgreSQL TLS server"
docker run --detach --name "$postgres_container" \
    --network "$test_network" --network-alias postgres-tls.test --network-alias wrong-postgres-tls.test \
    --tmpfs /var/lib/postgresql/data \
    --mount "type=bind,source=$temporary_dir,target=/source,readonly" \
    --env POSTGRES_DB=aiops_test --env POSTGRES_USER=aiops --env "POSTGRES_PASSWORD=$database_password" \
    --entrypoint sh "$postgres_image" -ceu '
        install -d -o postgres -g postgres -m 0700 /run/postgres-tls
        cp /source/server.crt /run/postgres-tls/server.crt
        cp /source/server.key /run/postgres-tls/server.key
        chown postgres:postgres /run/postgres-tls/server.crt /run/postgres-tls/server.key
        chmod 0644 /run/postgres-tls/server.crt
        chmod 0600 /run/postgres-tls/server.key
        exec docker-entrypoint.sh postgres -c ssl=on -c ssl_min_protocol_version=TLSv1.2 \
            -c ssl_cert_file=/run/postgres-tls/server.crt -c ssl_key_file=/run/postgres-tls/server.key
    ' >/dev/null

ready=false
stage="waiting for temporary PostgreSQL readiness"
for attempt in {1..60}; do
    if docker exec "$postgres_container" pg_isready -U aiops -d aiops_test >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    docker logs --tail=30 "$postgres_container" >&2
    echo "Temporary TLS PostgreSQL did not become ready." >&2
    exit 1
fi

stage="checking verified TLS and negative certificate cases"
docker run --rm --interactive --network "$test_network" --entrypoint python \
    --mount "type=bind,source=$trust_dir,target=/run/certs,readonly" \
    --env AIOPS_ENV=production \
    --env "DATABASE_PASSWORD=$database_password" \
    --env PGSSLROOTCERT=/run/certs/ca.crt \
    "$client_image" - <<'PY'
import os

import psycopg

from db_config import validate_database_url

password = os.environ["DATABASE_PASSWORD"]
base = f"postgresql://aiops:{password}@postgres-tls.test:5432/aiops_test"
valid_url = f"{base}?sslmode=verify-full&sslrootcert=/run/certs/ca.crt"
validate_database_url(valid_url, "DATABASE_URL", required=True)

with psycopg.connect(valid_url) as connection:
    ssl, protocol, cipher = connection.execute(
        "SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
    ).fetchone()
    assert ssl and protocol and cipher
    print(f"Verified PostgreSQL TLS handshake: {protocol}, {cipher}.")

for name, url in (
    ("untrusted CA", f"{base}?sslmode=verify-full&sslrootcert=/run/certs/wrong-ca.crt"),
    ("hostname mismatch", valid_url.replace("postgres-tls.test", "wrong-postgres-tls.test")),
):
    try:
        psycopg.connect(url)
    except psycopg.OperationalError:
        print(f"Rejected PostgreSQL TLS connection with {name}.")
    else:
        raise AssertionError(f"PostgreSQL TLS connection unexpectedly accepted {name}.")

try:
    validate_database_url(f"{base}?sslmode=require", "DATABASE_URL", required=True)
except RuntimeError as error:
    assert "sslmode=verify-full" in str(error)
    print("Rejected non-verify-full PostgreSQL URL before connecting.")
else:
    raise AssertionError("Non-verify-full PostgreSQL URL unexpectedly passed production policy.")
PY

echo "PostgreSQL production TLS verifier passed using an internal Docker network and temporary data only."
