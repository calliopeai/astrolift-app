"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  BarChart3Icon,
  BrainIcon,
  ScrollIcon,
  ZapIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

type Tab = "metrics" | "logs" | "token-usage" | "reasoning";
const TABS: readonly Tab[] = ["metrics", "logs", "token-usage", "reasoning"];
const TAB_LABELS: Record<Tab, string> = {
  metrics: "Metrics",
  logs: "Logs",
  "token-usage": "Token Usage",
  reasoning: "Reasoning Traces",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  metrics: <BarChart3Icon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  "token-usage": <ZapIcon className="size-4" />,
  reasoning: <BrainIcon className="size-4" />,
};
const TAB_DESCRIPTIONS: Record<Tab, string> = {
  metrics:
    "Agent dispatch rate, run duration (p50/p95), retry rate, success vs failure counts, and active run counts — grouped by workload and org.",
  logs:
    "Container stdout/stderr for agent workload pods. Filter by agent workload, run ID, or time range. Secrets are redacted at ingest.",
  "token-usage":
    "Input and output token consumption per agent run, aggregated by workload and time window. Feeds cost attribution and quota enforcement.",
  reasoning:
    "Step-by-step reasoning traces for agent runs — tool calls, intermediate outputs, retry attempts, and final result. Linked to the full run record.",
};

export default function ObserveAgentsPage() {
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
      title="Observe · Agents"
      description="Performance, logs, token consumption, and reasoning traces for all agent dispatch workloads."
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
        title={`Agent ${TAB_LABELS[tab]}`}
        description={TAB_DESCRIPTIONS[tab]}
      />
    </PageShell>
  );
}
