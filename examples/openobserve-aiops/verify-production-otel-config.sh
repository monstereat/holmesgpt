#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/holmesgpt-otel-config-check.XXXXXX")"
image="otel/opentelemetry-collector-contrib@sha256:fd328de2552466ad78385e1b1289c3f2402b1c45f265b252aab1955b42845ac1"
cleanup() {
    rm -rf -- "$temp_dir"
}
trap cleanup EXIT

openssl req -x509 -newkey rsa:2048 -nodes -keyout "$temp_dir/receiver.key" \
    -out "$temp_dir/receiver.crt" -days 1 -subj '/CN=otel-config-check' \
    >/dev/null 2>&1
printf 'short-lived-validation-token\n' > "$temp_dir/receiver.token"
printf 'validation-writer\n' > "$temp_dir/writer.username"
printf 'short-lived-validation-password\n' > "$temp_dir/writer.password"
mkdir -m 700 "$temp_dir/queue"
chmod 600 "$temp_dir"/receiver.* "$temp_dir"/writer.*

docker run --rm --network none \
    --mount "type=bind,src=$repo_dir/otel-collector-production.yaml,dst=/etc/otelcol-contrib/config.yaml,readonly" \
    --mount "type=bind,src=$temp_dir,dst=/run/otel-secrets,readonly" \
    --mount "type=bind,src=$temp_dir/queue,dst=/var/lib/otel-queue" \
    -e OTEL_RECEIVER_TOKEN_FILE=/run/otel-secrets/receiver.token \
    -e OTEL_RECEIVER_CERT_FILE=/run/otel-secrets/receiver.crt \
    -e OTEL_RECEIVER_KEY_FILE=/run/otel-secrets/receiver.key \
    -e OPENOBSERVE_WRITER_USERNAME_FILE=/run/otel-secrets/writer.username \
    -e OPENOBSERVE_WRITER_PASSWORD_FILE=/run/otel-secrets/writer.password \
    -e OTEL_MEMORY_LIMIT_MIB=256 \
    -e OTEL_MEMORY_SPIKE_LIMIT_MIB=64 \
    -e OTEL_BATCH_SIZE=256 \
    -e OTEL_EXPORT_QUEUE_SIZE=1000 \
    -e OTEL_QUEUE_STORAGE_DIR=/var/lib/otel-queue \
    -e OTEL_QUEUE_FILE_MAX_BYTES=536870912 \
    -e OTEL_EXPORT_RETRY_MAX_ELAPSED_TIME=5m \
    -e OTEL_METRICS_BIND_ADDRESS=127.0.0.1 \
    -e OPENOBSERVE_HOST=observe.example.invalid \
    -e OPENOBSERVE_ORG=validation \
    -e OPENOBSERVE_OTLP_STREAM=validation \
    "$image" validate --config=file:/etc/otelcol-contrib/config.yaml

echo "Production OpenTelemetry Collector template validated with pinned image $image; no endpoint was contacted"
