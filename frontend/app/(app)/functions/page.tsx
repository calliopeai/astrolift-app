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
 * The runtime layer hasn't shipped, so the page is the function list
 * (spec 44 §5.1). The four signal placeholders folded in from
 * /observe/functions (#892) had no query behind them and a second row of
 * tabs; they return as the function's own Logs & metrics once the runtime
 * reports them.
 */
export default function FunctionsPage() {
  return <FunctionsClient />;
}
