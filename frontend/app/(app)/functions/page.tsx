"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BoltIcon,
  GaugeIcon,
  LinkIcon,
} from "lucide-react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";

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
 * Same image build pipeline, same cluster, same secrets model as every
 * other Astrolift workload. The runtime layer (Knative or equivalent)
 * handles cold starts, warm pools, and concurrency limits.
 */

type FunctionTab = "invocations" | "errors" | "throughput" | "triggers";
const TABS: readonly FunctionTab[] = ["invocations", "errors", "throughput", "triggers"];

const TAB_LABELS: Record<FunctionTab, string> = {
  invocations: "Invocations",
  errors: "Errors",
  throughput: "Throughput",
  triggers: "Triggers",
};

export default function FunctionsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as FunctionTab | null;
  const tab: FunctionTab = rawTab && TABS.includes(rawTab) ? rawTab : "invocations";

  function setTab(next: FunctionTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "invocations") {
      params.delete("tab");
    } else {
      params.set("tab", next);
    }
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Functions"
      description="Event-driven container invocations — scale to zero, trigger on HTTP, queues, or webhooks."
    >
      {/* Tab bar */}
      <div className="flex gap-1 border-b pb-0 mb-4">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={[
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              t === tab
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {/* Invocations — recent function calls */}
      {tab === "invocations" && (
        <EmptyState
          icon={<BoltIcon className="size-5" />}
          title="No invocations yet"
          description="Recent function invocations — function name, app, status, HTTP status code, duration, and trigger source — will appear here once functions are deployed to the tenant cluster."
        />
      )}

      {/* Errors — failed invocations */}
      {tab === "errors" && (
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title="No errors"
          description="Failed invocations with error type, stack trace, and trigger context will appear here."
        />
      )}

      {/* Throughput — golden signals per function from Prometheus */}
      {tab === "throughput" && (
        <EmptyState
          icon={<GaugeIcon className="size-5" />}
          title="No throughput data"
          description="Throughput metrics from Prometheus — connect an observability backend to view golden signals (p99 latency, error rate, requests/sec) per function."
        />
      )}

      {/* Triggers — configured event sources per function */}
      {tab === "triggers" && (
        <EmptyState
          icon={<LinkIcon className="size-5" />}
          title="No function workloads"
          description="Function workloads declared as kind: function in astrolift.toml will appear here with their HTTP endpoint URLs and configured event sources."
        />
      )}
    </PageShell>
  );
}
