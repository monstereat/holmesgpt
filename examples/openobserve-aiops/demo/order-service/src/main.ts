/**
 * Demo order service.
 *
 * - Traces: OpenTelemetry auto-instrumentation -> OpenObserve OTLP endpoint.
 * - Logs:   pino (console) plus a batching ingester that writes JSON records
 *           enriched with trace_id/span_id into the `app_logs` stream.
 * - Frontend errors from the demo page are persisted into the
 *           `frontend_errors` stream with the same trace_id.
 * - CHAOS_MODE=on simulates the bad release: POST /orders fails with 500.
 *
 * Start with `node dist/main.js` (runs otel.ts first via main.js import order).
 */
import "./otel"; // must be first
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import "reflect-metadata";
import type { Request } from "express";
import { NestFactory } from "@nestjs/core";
import {
  Body,
  BadRequestException,
  Controller,
  Header,
  Logger,
  Module,
  Post,
  Get,
  Injectable,
  RawBodyRequest,
  Req,
  ServiceUnavailableException,
  UnauthorizedException,
} from "@nestjs/common";
import { trace, context, SpanStatusCode } from "@opentelemetry/api";
import { OpenObserveIngester } from "./ingester";
import { normalizeReleaseEvent, verifyReleaseSignature } from "./release-webhook";

const OO_URL = process.env.OPENOBSERVE_URL || "http://openobserve:5080";
const OO_ORG = process.env.OPENOBSERVE_ORG || "default";
const OO_USER = process.env.OPENOBSERVE_USER || "";
const OO_PASSWORD = process.env.OPENOBSERVE_PASSWORD || "";
const RELEASE = process.env.RELEASE_VERSION || "v1.0.0";
const CHAOS = (process.env.CHAOS_MODE || "off").toLowerCase() === "on";
const RELEASE_WEBHOOK_SECRET = process.env.RELEASE_WEBHOOK_SECRET || "";

const ingester = new OpenObserveIngester(OO_URL, OO_ORG, OO_USER, OO_PASSWORD);

function currentTrace(): { trace_id: string; span_id: string } {
  const span = trace.getSpan(context.active());
  const sc = span?.spanContext();
  return {
    trace_id: sc ? sc.traceId : "",
    span_id: sc ? sc.spanId : "",
  };
}

function emitLog(
  level: "info" | "error" | "warn",
  message: string,
  fields: Record<string, unknown> = {},
): void {
  const { trace_id, span_id } = currentTrace();
  const record = {
    _stream: "app_logs",
    _timestamp: Date.now() * 1000, // microseconds
    level,
    message,
    service: "order-service",
    release: RELEASE,
    trace_id,
    span_id,
    ...fields,
  };
  // Local stdout for humans; ingester for OpenObserve.
  process.stdout.write(JSON.stringify(record) + "\n");
  ingester.add(record);
}

@Injectable()
export class OrdersService {
  private readonly logger = new Logger(OrdersService.name);

  create(order: { sku: string; quantity: number; userId: string }): Record<string, unknown> {
    emitLog("info", "order create requested", {
      route: "POST /orders",
      sku: order.sku,
      quantity: order.quantity,
      user_id: order.userId,
    });

    if (CHAOS) {
      // Simulated bad release: inventory lookup throws and the request 500s.
      const err = new Error(
        `inventory lookup failed for sku=${order.sku}: connection refused (simulated bad release ${RELEASE})`,
      );
      const span = trace.getSpan(context.active());
      span?.recordException(err);
      span?.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
      emitLog("error", "inventory lookup failed", {
        route: "POST /orders",
        sku: order.sku,
        error: err.message,
        stack: err.stack?.split("\n").slice(0, 5).join(" | "),
      });
      throw err;
    }

    const order_id = `ord-${Math.random().toString(36).slice(2, 10)}`;
    emitLog("info", "order created", {
      route: "POST /orders",
      order_id,
      sku: order.sku,
    });
    return { order_id, status: "created", release: RELEASE };
  }

  ingestFrontendError(payload: Record<string, unknown>): void {
    const record = {
      _stream: "frontend_errors",
      _timestamp: Date.now() * 1000,
      service: "web-frontend",
      release: RELEASE,
      message: String(payload.message || "unknown frontend error"),
      stack: String(payload.stack || ""),
      route: String(payload.route || ""),
      trace_id: String(payload.trace_id || ""),
      span_id: "",
      user_agent: String(payload.user_agent || ""),
    };
    ingester.add(record);
  }

  ingestReleaseEvent(event: {
    service: string;
    version: string;
    commit_sha: string;
    changed_files: string[];
    deployed_at: string;
  }): void {
    ingester.add({
      _stream: "app_logs",
      _timestamp: Date.parse(event.deployed_at) * 1000,
      level: "info",
      message: "release deployed",
      event_type: "release_deployed",
      service: event.service,
      release: event.version,
      commit_sha: event.commit_sha,
      changed_files: event.changed_files,
      deployed_at: event.deployed_at,
      trace_id: "",
      span_id: "",
    });
  }

  demoState(): { release: string; chaos_mode: string } {
    return { release: RELEASE, chaos_mode: CHAOS ? "on" : "off" };
  }

  flush(): Promise<void> {
    return ingester.flush();
  }
}

@Controller()
class DemoController {
  constructor(private readonly orders: OrdersService) {}

  @Get("/")
  index(): string {
    return readFileSync(join(__dirname, "..", "public", "index.html"), "utf8");
  }

  @Get("/monitoring.js")
  @Header("Content-Type", "application/javascript; charset=utf-8")
  monitoringSdk(): string {
    return readFileSync(join(__dirname, "..", "public", "monitoring.js"), "utf8");
  }

  @Get("/internal/demo-state")
  demoState(): { release: string; chaos_mode: string } {
    return this.orders.demoState();
  }

  @Post("/orders")
  create(
    @Body()
    body: { sku?: string; quantity?: number; userId?: string },
  ): Record<string, unknown> {
    return this.orders.create({
      sku: String(body.sku || "SKU-001"),
      quantity: Number(body.quantity ?? 1),
      userId: String(body.userId || "anonymous"),
    });
  }

  @Post("/internal/frontend-errors")
  frontendError(@Body() payload: Record<string, unknown>): { ok: true } {
    this.orders.ingestFrontendError(payload);
    return { ok: true };
  }

  @Post("/internal/releases")
  releaseEvent(
    @Req() request: RawBodyRequest<Request>,
    @Body() payload: unknown,
  ): { accepted: true } {
    if (!RELEASE_WEBHOOK_SECRET) throw new ServiceUnavailableException();
    if (!verifyReleaseSignature(
      request.rawBody, request.header("x-release-signature"), RELEASE_WEBHOOK_SECRET,
    )) {
      throw new UnauthorizedException();
    }
    const event = normalizeReleaseEvent(payload);
    if (!event) throw new BadRequestException("Invalid release event");
    this.orders.ingestReleaseEvent(event);
    return { accepted: true };
  }

  @Post("/internal/flush")
  async flushLogs(): Promise<{ ok: true }> {
    await this.orders.flush();
    return { ok: true };
  }
}

@Module({
  controllers: [DemoController],
  providers: [OrdersService],
})
class DemoModule {}

async function bootstrap(): Promise<void> {
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`${OO_URL}/healthz`, {
        signal: AbortSignal.timeout(1000),
      });
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {
      // OpenObserve is started by Compose and may need time for its first boot.
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  if (!ready) throw new Error("OpenObserve did not become ready within 60 seconds");

  const app = await NestFactory.create(DemoModule, { logger: false, rawBody: true });
  app.enableCors();
  await app.listen(8080);
  emitLog("info", "order-service started", {
    route: "-",
    chaos_mode: CHAOS ? "on" : "off",
    release: RELEASE,
  });
  process.stdout.write(
    `order-service listening on :8080 (CHAOS_MODE=${CHAOS ? "on" : "off"})\n`,
  );
  process.on("SIGINT", () => {
    ingester.flush().finally(() => process.exit(0));
  });
  process.on("SIGTERM", () => {
    ingester.flush().finally(() => process.exit(0));
  });
  void execSync; // keep import tree-shake safe
}

bootstrap().catch((err) => {
  process.stderr.write(`bootstrap failed: ${String(err)}\n`);
  process.exit(1);
});
