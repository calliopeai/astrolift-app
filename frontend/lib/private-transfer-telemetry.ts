import type { SpanJSON } from "@sentry/core";

export const PRIVATE_TRANSFER_QUERY =
  /[?&](?:x-amz-(?:signature|credential|security-token)|awsaccesskeyid|x-goog-(?:signature|credential))=/i;

function privateUrl(value: string) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (PRIVATE_TRANSFER_QUERY.test(value)) return true;
    try {
      const decoded = decodeURIComponent(value);
      if (decoded === value) break;
      value = decoded;
    } catch {
      break;
    }
  }
  return false;
}

export function containsPrivateTransferUrl(
  value: unknown,
  visited = new WeakSet<object>()
): boolean {
  if (typeof value === "string") return privateUrl(value);
  if (value === null || typeof value !== "object" || visited.has(value)) return false;
  visited.add(value);
  try {
    return Object.values(value).some((item) => containsPrivateTransferUrl(item, visited));
  } catch {
    return true;
  }
}

export function excludePrivateTransfer<T>(record: T): T | null {
  return containsPrivateTransferUrl(record) ? null : record;
}

export function privateTransferSpan(span: SpanJSON): SpanJSON {
  if (!containsPrivateTransferUrl(span)) return span;
  // The SDK requires a span here; retain timing and identifiers without the
  // request description, attributes or links that can carry private grants.
  return {
    trace_id: span.trace_id,
    span_id: span.span_id,
    parent_span_id: span.parent_span_id,
    start_timestamp: span.start_timestamp,
    timestamp: span.timestamp,
    op: "http.client",
    description: "Private model file transfer",
    data: {},
  };
}
