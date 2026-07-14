import { FunctionsClient } from "./functions-client";

export const metadata = { title: "Functions · Astrolift" };

/**
 * Functions — event-driven, short-lived container invocations.
 *
 * The seventh Astrolift runtime primitive alongside Apps, Agents,
 * Workflows, Jobs, Tasks, and scheduled Deployments.
 *
 * Functions differ from Tasks in trigger model and scale behavior:
 * - Trigger: HTTP request, queue message, webhook, or event
 * - Duration: milliseconds to seconds (not minutes)
 * - Scale: horizontal to zero between invocations (Knative Serving)
 * - Billing model: per-invocation, not per-pod-hour
 *
 * The runtime layer hasn't shipped: the signal tabs (throughput / errors /
 * latency / logs, folded in from /observe/functions — #892) are gateway
 * placeholders until a query backs them, and the copy says so.
 */
export default function FunctionsPage() {
  return <FunctionsClient />;
}
