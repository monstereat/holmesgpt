#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
client_image="${AIOPS_REDIS_TLS_CLIENT_IMAGE:-holmesgpt-aiops-goal-incident-test:latest}"
redis_image="redis:7.4.2-alpine"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/aiops-redis-tls.XXXXXX")"
redis_container="aiops-redis-tls-${temporary_dir##*.}"
test_network="${AIOPS_REDIS_TLS_TEST_NETWORK:-}"
stage="locating internal test network"

cleanup() {
    local exit_status=$?
    trap - EXIT
    if [[ "$exit_status" -ne 0 ]]; then
        echo "::error title=Redis TLS verifier::Failed during ${stage} (exit ${exit_status})."
    fi
    docker rm -f "$redis_container" >/dev/null 2>&1 || true
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
    echo "Start the Compose postgres-test service or set AIOPS_REDIS_TLS_TEST_NETWORK to its internal test network." >&2
    exit 1
fi
if [[ "$(docker network inspect "$test_network" --format '{{.Internal}}')" != true ]]; then
    echo "Redis TLS verification requires an internal Docker test network: $test_network" >&2
    exit 1
fi

stage="building current incident test image"
docker build --file "$script_dir/alert-trigger/Dockerfile" --target test --tag "$client_image" "$script_dir/alert-trigger"

stage="generating temporary certificate authority and server certificate"
umask 077
openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
    -keyout "$temporary_dir/ca.key" -out "$temporary_dir/ca.crt" \
    -subj "/CN=AIOps Redis TLS Test CA" >/dev/null 2>&1
openssl req -new -newkey rsa:2048 -nodes \
    -keyout "$temporary_dir/server.key" -out "$temporary_dir/server.csr" \
    -subj "/CN=redis-tls.test" \
    -addext "subjectAltName=DNS:redis-tls.test" >/dev/null 2>&1
cat >"$temporary_dir/server.ext" <<'EOF'
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:redis-tls.test
EOF
openssl x509 -req -in "$temporary_dir/server.csr" \
    -CA "$temporary_dir/ca.crt" -CAkey "$temporary_dir/ca.key" \
    -CAcreateserial -out "$temporary_dir/server.crt" -days 2 -sha256 \
    -extfile "$temporary_dir/server.ext" >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
    -keyout "$temporary_dir/wrong-ca.key" -out "$temporary_dir/wrong-ca.crt" \
    -subj "/CN=Wrong AIOps Redis TLS Test CA" >/dev/null 2>&1
trust_dir="$temporary_dir/trust"
mkdir -m 0755 "$trust_dir"
cp "$temporary_dir/ca.crt" "$trust_dir/ca.crt"
cp "$temporary_dir/wrong-ca.crt" "$trust_dir/wrong-ca.crt"
chmod 0644 "$trust_dir/ca.crt" "$trust_dir/wrong-ca.crt"
redis_password="$(openssl rand -hex 32)"

stage="starting temporary Redis TLS server"
docker run --detach --name "$redis_container" \
    --network "$test_network" --network-alias redis-tls.test --network-alias wrong-redis-tls.test \
    --mount "type=bind,source=$temporary_dir,target=/source,readonly" \
    --env "REDIS_PASSWORD=$redis_password" \
    --entrypoint sh "$redis_image" -ceu '
        install -d -o redis -g redis -m 0700 /run/redis-tls
        cp /source/server.crt /run/redis-tls/server.crt
        cp /source/server.key /run/redis-tls/server.key
        cp /source/ca.crt /run/redis-tls/ca.crt
        chown redis:redis /run/redis-tls/server.crt /run/redis-tls/server.key /run/redis-tls/ca.crt
        chmod 0644 /run/redis-tls/server.crt /run/redis-tls/ca.crt
        chmod 0600 /run/redis-tls/server.key
        exec docker-entrypoint.sh redis-server --port 0 --tls-port 6379 \
            --tls-cert-file /run/redis-tls/server.crt --tls-key-file /run/redis-tls/server.key \
            --tls-ca-cert-file /run/redis-tls/ca.crt --tls-auth-clients no \
            --requirepass "$REDIS_PASSWORD"
    ' >/dev/null

ready=false
stage="waiting for temporary Redis readiness"
for attempt in {1..60}; do
    if docker exec --env "REDISCLI_AUTH=$redis_password" "$redis_container" \
        redis-cli --tls --insecure -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q PONG; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    docker logs --tail=30 "$redis_container" >&2
    echo "Temporary TLS Redis did not become ready." >&2
    exit 1
fi

stage="checking verified Redis TLS and negative certificate cases"
docker run --rm --interactive --network "$test_network" --entrypoint python \
    --mount "type=bind,source=$trust_dir,target=/run/certs,readonly" \
    --env AIOPS_ENV=production \
    --env "REDIS_PASSWORD=$redis_password" \
    "$client_image" - <<'PY'
import os

from celery import Celery

from broker_config import validate_broker_url

password = os.environ["REDIS_PASSWORD"]
base = f"rediss://:{password}@redis-tls.test:6379/0"
valid_url = f"{base}?ssl_cert_reqs=required&ssl_check_hostname=true&ssl_ca_certs=/run/certs/ca.crt"


def connect_and_ping(url: str) -> tuple[Celery, object]:
    broker_url = validate_broker_url(url, required=True)
    app = Celery("aiops_redis_tls_verifier", broker=broker_url)
    try:
        connection = app.connection_for_write()
        connection.connect()
        client = connection.channel().client
        assert client.ping()
        return app, client
    except Exception:
        app.close()
        raise


app, client = connect_and_ping(valid_url)
redis_connection = client.connection_pool.get_connection()
try:
    redis_connection.connect()
    tls_version = redis_connection._sock.version()
    assert tls_version.startswith("TLSv"), "Redis connection did not negotiate TLS"
    print(f"Verified Celery/redis-py TLS handshake: {tls_version}.")
finally:
    client.connection_pool.release(redis_connection)
    app.close()

for name, url in (
    ("untrusted CA", f"{base}?ssl_cert_reqs=required&ssl_check_hostname=true&ssl_ca_certs=/run/certs/wrong-ca.crt"),
    ("hostname mismatch", valid_url.replace("redis-tls.test", "wrong-redis-tls.test")),
):
    try:
        app, _ = connect_and_ping(url)
    except Exception as error:
        print(f"Rejected Redis TLS connection with {name} ({type(error).__name__}).")
    else:
        app.close()
        raise AssertionError(f"Redis TLS connection unexpectedly accepted {name}.")

for name, url in (
    ("plaintext", base.replace("rediss://", "redis://", 1) + "?ssl_cert_reqs=required&ssl_check_hostname=true"),
    ("disabled hostname verification", f"{base}?ssl_cert_reqs=required&ssl_check_hostname=false&ssl_ca_certs=/run/certs/ca.crt"),
):
    try:
        validate_broker_url(url, required=True)
    except RuntimeError:
        print(f"Rejected Redis broker configuration with {name} before connecting.")
    else:
        raise AssertionError(f"Redis broker configuration unexpectedly accepted {name}.")
PY

echo "Redis production TLS verifier passed using an internal Docker network and temporary data only."
