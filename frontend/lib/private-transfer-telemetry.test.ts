import * as Sentry from "@sentry/browser";
import type { SpanJSON } from "@sentry/core";
import { describe, expect, it } from "vitest";
import {
  containsPrivateTransferUrl,
  excludePrivateTransfer,
  privateTransferSpan,
} from "./private-transfer-telemetry";

const privateUrl =
  "https://storage.fixture.invalid/object?X-Amz-Credential=credential-canary&X-Amz-Signature=signature-canary";

describe("private model transfer telemetry", () => {
  it.each([
    privateUrl,
    privateUrl.toLowerCase(),
    encodeURIComponent(privateUrl),
    encodeURIComponent(encodeURIComponent(privateUrl)),
    "https://storage.fixture.invalid/object?AWSAccessKeyId=credential-canary&Signature=signature-canary",
    "https://storage.fixture.invalid/object?X-Goog-Credential=credential-canary&X-Goog-Signature=signature-canary",
  ])("excludes signed capabilities in nested network records: %s", (url) => {
    const record = { data: { payload: [{ url }] } };
    expect(excludePrivateTransfer(record)).toBeNull();
  });

  it("preserves ordinary requests and tolerates circular metadata", () => {
    const record: { url: string; self?: unknown } = {
      url: "https://api.fixture.invalid/apps?page=2&search=signature",
    };
    record.self = record;
    expect(excludePrivateTransfer(record)).toBe(record);
    expect(containsPrivateTransferUrl({ nested: privateUrl, self: record })).toBe(true);
  });

  it("drops unreadable telemetry instead of failing the SDK callback", () => {
    const record = Object.defineProperty({}, "data", {
      enumerable: true,
      get() {
        throw new Error("Uninspectable record");
      },
    });
    expect(excludePrivateTransfer(record)).toBeNull();
  });

  it("retains span timing without descriptions, attributes or links carrying grants", () => {
    const span: SpanJSON = {
      trace_id: "a".repeat(32),
      span_id: "b".repeat(16),
      parent_span_id: "c".repeat(16),
      start_timestamp: 1,
      timestamp: 2,
      description: "PUT object",
      data: { "url.full": privateUrl },
    };
    const result = privateTransferSpan(span);
    expect(result).toMatchObject({
      trace_id: span.trace_id,
      span_id: span.span_id,
      start_timestamp: 1,
      timestamp: 2,
      data: {},
    });
    expect(JSON.stringify(result)).not.toContain("canary");
    expect(span.data["url.full"]).toBe(privateUrl);
    const ordinary = { ...span, data: { "url.full": "https://api.fixture.invalid/apps" } };
    expect(privateTransferSpan(ordinary)).toBe(ordinary);
  });

  it("filters actual SDK envelopes while preserving ordinary events and tracing", async () => {
    const envelopes: unknown[] = [];
    Sentry.init({
      dsn: "https://public@telemetry.fixture.invalid/1",
      defaultIntegrations: false,
      integrations: [Sentry.breadcrumbsIntegration()],
      tracesSampleRate: 1,
      beforeBreadcrumb: excludePrivateTransfer,
      beforeSend: excludePrivateTransfer,
      beforeSendTransaction: excludePrivateTransfer,
      beforeSendSpan: privateTransferSpan,
      transport: () => ({
        send: (envelope) => {
          envelopes.push(envelope);
          return Promise.resolve({ statusCode: 200 });
        },
        flush: () => Promise.resolve(true),
      }),
    });
    try {
      Sentry.addBreadcrumb({ category: "fetch", data: { url: privateUrl } });
      Sentry.addBreadcrumb({ category: "fetch", data: { url: "/ordinary-control-request" } });
      Sentry.captureMessage("ordinary-control-event");
      Sentry.captureMessage(`Private upload failed: ${privateUrl}`);
      Sentry.startSpan({ name: "ordinary-control-transaction", op: "file.import" }, () => {
        Sentry.startSpan(
          { name: `PUT ${privateUrl}`, op: "http.client", attributes: { "url.full": privateUrl } },
          () => {}
        );
      });
      expect(await Sentry.flush(2000)).toBe(true);
      const emitted = JSON.stringify(envelopes);
      expect(emitted).not.toContain("signature-canary");
      expect(emitted).not.toContain("credential-canary");
      expect(emitted).toContain("ordinary-control-event");
      expect(emitted).toContain("ordinary-control-request");
      expect(emitted).toContain("ordinary-control-transaction");
      expect(emitted).toContain("Private model file transfer");
    } finally {
      await Sentry.close(2000);
    }
  });
});
