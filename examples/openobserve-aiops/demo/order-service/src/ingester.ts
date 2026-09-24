/**
 * Batching JSON ingester for OpenObserve log streams.
 *
 * Writes newline-safe JSON records to /api/{org}/{stream}/_json with Basic
 * Auth. Records carry an explicit `trace_id` field so that the HolmesGPT
 * openobserve_find_trace tool can query them directly.
 */
import { randomUUID } from "node:crypto";

type LogRecord = Record<string, unknown>;

export class OpenObserveIngester {
  private buffer: LogRecord[] = [];
  private timer: NodeJS.Timeout | null = null;
  private readonly auth: string;
  private readonly baseUrl: string;

  constructor(
    baseUrl: string,
    org: string,
    user: string,
    password: string,
    private readonly flushIntervalMs = 1000,
    private readonly maxBatch = 50,
  ) {
    this.baseUrl = `${baseUrl.replace(/\/$/, "")}/api/${org}`;
    this.auth = Buffer.from(`${user}:${password}`).toString("base64");
  }

  add(record: LogRecord): void {
    this.buffer.push(record);
    if (this.buffer.length >= this.maxBatch) {
      void this.flush();
      return;
    }
    if (!this.timer) {
      this.timer = setTimeout(() => void this.flush(), this.flushIntervalMs);
    }
  }

  async flush(): Promise<void> {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    const batch = this.buffer.splice(0, this.buffer.length);
    if (batch.length === 0) return;

    const streams = new Map<string, LogRecord[]>();
    for (const record of batch) {
      const stream = String(record._stream || "app_logs");
      const list = streams.get(stream) || [];
      list.push(record);
      streams.set(stream, list);
    }

    await Promise.all(
      [...streams.entries()].map(([stream, records]) =>
        this.ingest(stream, records),
      ),
    );
  }

  private async ingest(stream: string, records: LogRecord[]): Promise<void> {
    try {
      const response = await fetch(`${this.baseUrl}/${stream}/_json`, {
        method: "POST",
        headers: {
          Authorization: `Basic ${this.auth}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(records),
        redirect: "error", // Never follow a redirect with ingest credentials.
      });
      if (!response.ok) {
        // Print the status only; response bodies may echo credentials.
        process.stderr.write(
          `[ingester] OpenObserve ${stream} ingest failed: HTTP ${response.status}\n`,
        );
      }
    } catch (err) {
      // Demo behaviour: logs are fire-and-forget; never crash the app.
      process.stderr.write(
        `[ingester] OpenObserve ${stream} ingest error: ${String(err)}\n`,
      );
    }
  }

  newCorrelationId(): string {
    return randomUUID().replace(/-/g, "");
  }
}
