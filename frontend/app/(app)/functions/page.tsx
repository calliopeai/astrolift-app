"use client";

import { BoltIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

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
 * Not shipped yet: every tab was a placeholder EmptyState with no backing
 * query (#906), which read as a first-class, working primitive. Gated to a
 * single honest "coming soon" surface until a query backs it.
 */
export default function FunctionsPage() {
  return (
    <PageShell
      title="Functions"
      description="Event-driven container invocations — scale to zero, trigger on HTTP, queues, or webhooks."
    >
      <EmptyState
        icon={<BoltIcon className="size-5" />}
        title="Functions — coming soon"
        description="Event-driven, scale-to-zero function invocations aren't available yet. Invocations, errors, throughput, and per-function triggers will surface here once the runtime layer ships."
      />
    </PageShell>
  );
}
