"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircleIcon,
  ClockIcon,
  ScrollIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

type Tab = "history" | "logs" | "failures";
const TABS: readonly Tab[] = ["history", "logs", "failures"];
const TAB_LABELS: Record<Tab, string> = {
  history: "Run History",
  logs: "Logs",
  failures: "Failures",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  history: <ClockIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  failures: <AlertCircleIcon className="size-4" />,
};
const TAB_DESCRIPTIONS: Record<Tab, string> = {
  history:
    "Execution history for all cronjob and one-shot job workloads — run time, exit code, duration, and triggering actor. Filter by app, schedule, or outcome.",
  logs:
    "Stdout/stderr output from completed job runs. Each run's log tail is stored inline; longer logs are streamed from the cluster on demand.",
  failures:
    "Failed job runs with exit code, error summary, and last log excerpt. Use to identify recurring failures and configure alerts on job failure rate.",
};

export default function ObserveJobsPage() {
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
      title="Observe · Jobs"
      description="Run history, logs, and failure analysis for all cronjob and one-shot task workloads."
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
        title={`Job ${TAB_LABELS[tab]}`}
        description={TAB_DESCRIPTIONS[tab]}
        actionHref="/jobs"
        actionLabel="Go to Jobs"
      />
    </PageShell>
  );
}
