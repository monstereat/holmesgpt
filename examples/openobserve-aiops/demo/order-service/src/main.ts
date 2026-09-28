/**
 * Demo order service.
 *
 * - Traces: OpenTelemetry auto-instrumentation -> OpenObserve OTLP endpoint.
 * - Logs:   pino (console) plus a batching ingester that writes JSON records
 *           enriched with trace_id/span_id into the `app_logs` stream.
 * - Frontend errors from the demo page are persisted into the
 *           `frontend_errors` stream with the same trace_id.
 * - CHAOS_MODE seeds the initial state when the persistent action volume is empty.
 *
 * Start with `node dist/main.js` (runs otel.ts first via main.js import order).
 */
import "./otel"; // must be first
import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { closeSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import "reflect-metadata";
import type { Request } from "express";
import { NestFactory } from "@nestjs/core";
import {
  Body,
  BadRequestException,
  ConflictException,
  Controller,
  Header,
  Headers,
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
const ORDER_ACTION_TOKEN = process.env.ORDER_ACTION_TOKEN || "";
const ORDER_ACTION_STATE_PATH = process.env.ORDER_ACTION_STATE_PATH || "/var/lib/order-service/test-action-state.json";
const MAX_PERSISTED_TEST_ACTIONS = 10_000;

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
  private chaosMode = CHAOS;
  private readonly testActions = new Map<
    string,
    { fingerprint: string; result: Record<string, unknown> }
  >();

  constructor() {
    this.loadActionState();
  }

  create(order: { sku: string; quantity: number; userId: string }): Record<string, unknown> {
    emitLog("info", "order create requested", {
      route: "POST /orders",
      sku: order.sku,
      quantity: order.quantity,
      user_id: order.userId,
    });

    if (this.chaosMode) {
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
    return { release: RELEASE, chaos_mode: this.chaosMode ? "on" : "off" };
  }

  applyTestAction(
    token: string | undefined,
    idempotencyKey: string | undefined,
    body: unknown,
  ): Record<string, unknown> {
    if (!ORDER_ACTION_TOKEN) {
      throw new ServiceUnavailableException();
    }
    if (!token || !this.matchesToken(token, ORDER_ACTION_TOKEN)) {
      throw new UnauthorizedException();
    }
    if (typeof idempotencyKey !== "string" || !/^[A-Za-z0-9:_-]{1,128}$/.test(idempotencyKey)) {
      throw new BadRequestException("A valid Idempotency-Key is required");
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      throw new BadRequestException("Action body must be an object");
    }

    const action = body as Record<string, unknown>;
    if (
      Object.keys(action).some((key) => !["action", "resource", "enabled"].includes(key))
      || action.action !== "set-chaos-mode"
      || action.resource !== "order-service"
      || typeof action.enabled !== "boolean"
    ) {
      throw new BadRequestException("Unsupported test action or parameters");
    }

    const fingerprint = JSON.stringify({
      action: action.action,
      resource: action.resource,
      enabled: action.enabled,
    });
    const previous = this.testActions.get(idempotencyKey);
    if (previous) {
      if (previous.fingerprint !== fingerprint) {
        throw new ConflictException("Idempotency-Key was already used for another action");
      }
      return { ...previous.result, duplicate: true };
    }
    if (this.testActions.size >= MAX_PERSISTED_TEST_ACTIONS) {
      throw new ServiceUnavailableException("The durable test-action journal is full; operator intervention is required");
    }

    const nextChaosMode = action.enabled;
    const result = {
      accepted: true,
      action_id: idempotencyKey,
      action: "set-chaos-mode",
      resource: "order-service",
      chaos_mode: nextChaosMode ? "on" : "off",
      duplicate: false,
    };
    const nextActions = new Map(this.testActions);
    nextActions.set(idempotencyKey, { fingerprint, result });
    try {
      this.persistActionState(nextChaosMode, nextActions);
    } catch {
      this.loadActionState();
      throw new ServiceUnavailableException("The durable test-action journal could not be committed");
    }
    this.chaosMode = nextChaosMode;
    this.testActions.set(idempotencyKey, { fingerprint, result });
    return result;
  }

  private loadActionState(): void {
    let contents: string;
    try {
      contents = readFileSync(ORDER_ACTION_STATE_PATH, "utf8");
    } catch (error) {
      if (typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT") {
        this.persistActionState(this.chaosMode, this.testActions);
        return;
      }
      throw new Error("Could not read the durable test-action journal");
    }

    let value: unknown;
    try {
      value = JSON.parse(contents);
    } catch {
      throw new Error("The durable test-action journal is invalid JSON");
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("The durable test-action journal has an invalid format");
    }
    const state = value as Record<string, unknown>;
    const operations = state.operations;
    if (
      state.schema_version !== 1
      || !["on", "off"].includes(String(state.chaos_mode))
      || !operations || typeof operations !== "object" || Array.isArray(operations)
    ) {
      throw new Error("The durable test-action journal has an unsupported format");
    }
    const entries = Object.entries(operations as Record<string, unknown>);
    if (entries.length > MAX_PERSISTED_TEST_ACTIONS) {
      throw new Error("The durable test-action journal exceeds its supported size");
    }
    const restored = new Map<string, { fingerprint: string; result: Record<string, unknown> }>();
    for (const [key, rawOperation] of entries) {
      if (!rawOperation || typeof rawOperation !== "object" || Array.isArray(rawOperation)) {
        throw new Error("The durable test-action journal contains an invalid operation");
      }
      const operation = rawOperation as Record<string, unknown>;
      const result = operation.result;
      const resultRecord = result && typeof result === "object" && !Array.isArray(result)
        ? result as Record<string, unknown>
        : null;
      if (
        !/^[A-Za-z0-9:_-]{1,128}$/.test(key) || typeof operation.fingerprint !== "string"
        || !resultRecord
        || resultRecord.accepted !== true
        || resultRecord.action_id !== key
        || resultRecord.action !== "set-chaos-mode"
        || resultRecord.resource !== "order-service"
        || !["on", "off"].includes(String(resultRecord.chaos_mode))
        || operation.fingerprint !== JSON.stringify({
          action: "set-chaos-mode",
          resource: "order-service",
          enabled: resultRecord.chaos_mode === "on",
        })
      ) {
        throw new Error("The durable test-action journal contains an invalid operation");
      }
      restored.set(key, { fingerprint: operation.fingerprint, result: resultRecord });
    }

    this.chaosMode = state.chaos_mode === "on";
    this.testActions.clear();
    for (const [key, operation] of restored) this.testActions.set(key, operation);
  }

  private persistActionState(
    chaosMode: boolean,
    actions: Map<string, { fingerprint: string; result: Record<string, unknown> }>,
  ): void {
    const directory = dirname(ORDER_ACTION_STATE_PATH);
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const temporaryPath = join(directory, `.${basename(ORDER_ACTION_STATE_PATH)}.${process.pid}.${randomUUID()}.tmp`);
    let fileDescriptor: number | undefined;
    try {
      fileDescriptor = openSync(temporaryPath, "wx", 0o600);
      writeFileSync(fileDescriptor, JSON.stringify({
        schema_version: 1,
        chaos_mode: chaosMode ? "on" : "off",
        operations: Object.fromEntries(actions),
      }));
      fsyncSync(fileDescriptor);
      closeSync(fileDescriptor);
      fileDescriptor = undefined;
      renameSync(temporaryPath, ORDER_ACTION_STATE_PATH);
      const directoryDescriptor = openSync(directory, "r");
      try {
        fsyncSync(directoryDescriptor);
      } finally {
        closeSync(directoryDescriptor);
      }
    } catch (error) {
      if (fileDescriptor !== undefined) closeSync(fileDescriptor);
      try {
        unlinkSync(temporaryPath);
      } catch {
        // The temporary file may already have been renamed.
      }
      throw error;
    }
  }

  testActionState(token: string | undefined): Record<string, string> {
    if (!ORDER_ACTION_TOKEN) {
      throw new ServiceUnavailableException();
    }
    if (!token || !this.matchesToken(token, ORDER_ACTION_TOKEN)) {
      throw new UnauthorizedException();
    }
    return { resource: "order-service", chaos_mode: this.chaosMode ? "on" : "off" };
  }

  private matchesToken(candidate: string, expected: string): boolean {
    const candidateDigest = createHash("sha256").update(candidate).digest();
    const expectedDigest = createHash("sha256").update(expected).digest();
    return timingSafeEqual(candidateDigest, expectedDigest);
  }

  flush(): Promise<void> {
    return ingester.flush();
  }
}

@Controller()
export class DemoController {
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

  @Post("/internal/test-actions")
  testAction(
    @Headers("x-order-action-token") token: string | undefined,
    @Headers("idempotency-key") idempotencyKey: string | undefined,
    @Body() body: unknown,
  ): Record<string, unknown> {
    return this.orders.applyTestAction(token, idempotencyKey, body);
  }

  @Get("/internal/test-actions/state")
  testActionState(
    @Headers("x-order-action-token") token: string | undefined,
  ): Record<string, string> {
    return this.orders.testActionState(token);
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
  const initialChaosMode = app.get(OrdersService).demoState().chaos_mode;
  emitLog("info", "order-service started", {
    route: "-",
    chaos_mode: initialChaosMode,
    release: RELEASE,
  });
  process.stdout.write(
    `order-service listening on :8080 (chaos_mode=${initialChaosMode})\n`,
  );
  process.on("SIGINT", () => {
    ingester.flush().finally(() => process.exit(0));
  });
  process.on("SIGTERM", () => {
    ingester.flush().finally(() => process.exit(0));
  });
}

if (require.main === module) {
  bootstrap().catch((err) => {
    process.stderr.write(`bootstrap failed: ${String(err)}\n`);
    process.exit(1);
  });
}
