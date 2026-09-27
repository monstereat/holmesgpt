/**
 * OpenTelemetry bootstrap. Import this module FIRST, before Nest.
 *
 * Traces go to OpenObserve via OTLP HTTP (protobuf). The W3C traceparent
 * header is honoured from upstream (frontend SDK), so browser errors and
 * server-side spans share one 32-hex trace ID.
 */
import { NodeSDK } from "@opentelemetry/sdk-node";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { resourceFromAttributes } from "@opentelemetry/resources";

const ooUrl = process.env.OPENOBSERVE_URL || "http://openobserve:5080";
const ooOrg = process.env.OPENOBSERVE_ORG || "default";
const ooUser = process.env.OPENOBSERVE_USER || "";
const ooPassword = process.env.OPENOBSERVE_PASSWORD || "";
const auth = Buffer.from(`${ooUser}:${ooPassword}`).toString("base64");

const sdk = new NodeSDK({
  resource: resourceFromAttributes({
    "service.name": process.env.OTEL_SERVICE_NAME || "order-service",
    "service.version": process.env.RELEASE_VERSION || "v1.0.0",
  }),
  traceExporter: new OTLPTraceExporter({
    url: `${ooUrl}/api/${ooOrg}/v1/traces`,
    headers: {
      Authorization: `Basic ${auth}`,
      "stream-name": "default",
      // Never let an ingest credential follow a redirect.
      "X-OO-No-Redirect": "1",
    },
  }),
  instrumentations: [
    getNodeAutoInstrumentations({
      // The demo page is served by us; do not trace our own log flushes.
      "@opentelemetry/instrumentation-fs": { enabled: false },
    }),
  ],
});

sdk.start();

process.on("SIGTERM", () => {
  sdk.shutdown().finally(() => process.exit(0));
});
