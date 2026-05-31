"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircleIcon,
  BarChart3Icon,
  GaugeIcon,
  ScrollIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

type Tab = "throughput" | "errors" | "latency" | "logs";
const TABS: readonly Tab[] = ["throughput", "errors", "latency", "logs"];
const TAB_LABELS: Record<Tab, string> = {
  throughput: "Throughput",
  errors: "Errors",
  latency: "Latency",
  logs: "Logs",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  throughput: <BarChart3Icon className="size-4" />,
  errors: <AlertCircleIcon className="size-4" />,
  latency: <GaugeIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
};
const TAB_DESCRIPTIONS: Record<Tab, string> = {
  throughput:
    "Requests per second and invocation count across all function workloads — broken down by trigger source (HTTP, queue, event, schedule) and app.",
  errors:
    "Failed invocations with HTTP status code, error type, and trigger context. Rate and volume charts to identify spikes and correlate with deploys.",
  latency:
    "Cold start frequency, warm invocation latency (p50/p95/p99), and end-to-end duration from trigger to response — the golden signals for function workloads.",
  logs:
    "Invocation logs from function containers. Includes request context, response code, and stdout from the handler. Secrets are redacted at ingest.",
};

export default function ObserveFunctionsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as Tab | null;
  const tab: Tab = rawTab && TABS.includes(rawTab) ? rawTab : "throughput";

  function setTab(next: Tab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "throughput") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Observe · Functions"
      description="Throughput, error rates, latency golden signals, and logs for all function (scale-to-zero) workloads."
    >
      <div className="flex gap-1 border-b pb-0 mb-4">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={[
              "flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              t === tab
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            {TAB_ICONS[t]}
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      <EmptyState
        icon={TAB_ICONS[tab]}
        title={`Function ${TAB_LABELS[tab]}`}
        description={TAB_DESCRIPTIONS[tab]}
        actionHref="/functions"
        actionLabel="Go to Functions"
      />
    </PageShell>
  );
}
