"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ClockIcon,
  ScrollIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

type Tab = "history" | "logs";
const TABS: readonly Tab[] = ["history", "logs"];
const TAB_LABELS: Record<Tab, string> = {
  history: "Run History",
  logs: "Logs",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  history: <ClockIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
};
const TAB_DESCRIPTIONS: Record<Tab, string> = {
  history:
    "Operator-initiated one-off task executions — command run, status, exit code, duration, and triggering user. Tasks are the one-shot equivalent of scheduled jobs.",
  logs:
    "Stdout/stderr from task run containers. Stored inline for quick inspection; full log available from the cluster for long-running tasks.",
};

export default function ObserveTasksPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as Tab | null;
  const tab: Tab = rawTab && TABS.includes(rawTab) ? rawTab : "history";

  function setTab(next: Tab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "history") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Observe · Tasks"
      description="Run history and logs for operator-initiated one-off task workloads."
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
        title={`Task ${TAB_LABELS[tab]}`}
        description={TAB_DESCRIPTIONS[tab]}
        actionHref="/tasks"
        actionLabel="Go to Tasks"
      />
    </PageShell>
  );
}
