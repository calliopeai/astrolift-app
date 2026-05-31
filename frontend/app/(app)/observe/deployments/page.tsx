"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ActivityIcon,
  BarChart3Icon,
  GitBranchIcon,
  ScrollIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

type Tab = "metrics" | "logs" | "traces";
const TABS: readonly Tab[] = ["metrics", "logs", "traces"];
const TAB_LABELS: Record<Tab, string> = {
  metrics: "Metrics",
  logs: "Logs",
  traces: "Traces",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  metrics: <BarChart3Icon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  traces: <GitBranchIcon className="size-4" />,
};

const TAB_DESCRIPTIONS: Record<Tab, string> = {
  metrics:
    "Rollout success rate, replica health, request latency (p50/p95/p99), error rate, and pod restart counts — aggregated across all deployment workloads.",
  logs:
    "Fleet-wide log search across all deployment container stdout/stderr. Filter by app, workload, severity, or time range. Results link to the live pod stream.",
  traces:
    "Distributed trace explorer for deployment workloads. Understand request latency, downstream errors, and service dependencies across all your running services.",
};

export default function ObserveDeploymentsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as Tab | null;
  const tab: Tab = rawTab && TABS.includes(rawTab) ? rawTab : "metrics";

  function setTab(next: Tab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "metrics") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Observe · Deployments"
      description="Health, performance, and log signals for all deployment workloads across the fleet."
    >
      {/* Tab bar */}
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
        title={`Deployment ${TAB_LABELS[tab]}`}
        description={TAB_DESCRIPTIONS[tab]}
        actionHref={tab === "logs" ? "/logs" : tab === "traces" ? "/traces" : "/metrics"}
        actionLabel={`Open ${TAB_LABELS[tab]} explorer`}
      />
    </PageShell>
  );
}
