"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@apollo/client/react";
import { AlertCircleIcon, CalendarClockIcon, ClockIcon, LayersIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

type Tab = "fleet" | "history" | "logs" | "failures";
const TABS: readonly Tab[] = ["fleet", "history", "logs", "failures"];
const TAB_LABELS: Record<Tab, string> = { fleet: "Fleet", history: "Run History", logs: "Logs", failures: "Failures" };
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  history: <ClockIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  failures: <AlertCircleIcon className="size-4" />,
};

function FleetTab() {
  const { data, loading } = useQuery<{ astroliftWorkloads: AstroliftWorkload[] }>(
    LIST_WORKLOADS, { variables: {}, fetchPolicy: "cache-and-network" }
  );
  const workloads = (data?.astroliftWorkloads ?? []).filter(
    (w) => w.kind === "cronjob" || w.kind === "job"
  );

  if (loading && workloads.length === 0)
    return <div className="space-y-2"><Skeleton className="h-12 w-full" /><Skeleton className="h-12 w-full" /></div>;

  if (workloads.length === 0)
    return <EmptyState icon={<CalendarClockIcon className="size-5" />} title="No job workloads"
      description="Apps with kind: cronjob or job in their manifest will appear here." actionHref="/apps" actionLabel="Browse apps" />;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Workload</TableHead>
          <TableHead>App</TableHead>
          <TableHead>Kind</TableHead>
          <TableHead>Schedule</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {workloads.map((w) => (
          <TableRow key={w.id}>
            <TableCell className="font-medium">
              <Link href={`/apps/${w.registeredAppSlug}/workloads`} className="hover:underline">{w.name}</Link>
            </TableCell>
            <TableCell><Badge variant="outline">{w.registeredAppSlug}</Badge></TableCell>
            <TableCell className="text-muted-foreground text-sm capitalize">{w.kind}</TableCell>
            <TableCell className="font-mono text-xs text-muted-foreground">{w.schedule || "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function ObserveJobsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const rawTab = searchParams.get("tab") as Tab | null;
  const tab: Tab = rawTab && TABS.includes(rawTab) ? rawTab : "fleet";

  function setTab(next: Tab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "fleet") params.delete("tab"); else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell title="Observe · Jobs" description="Fleet of scheduled and one-shot job workloads, run history, and failure analysis.">
      <div className="flex gap-1 border-b pb-0 mb-4">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={["flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              t === tab ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"].join(" ")}>
            {TAB_ICONS[t]} {TAB_LABELS[t]}
          </button>
        ))}
      </div>
      {tab === "fleet" ? <FleetTab /> :
       tab === "history" ? (
         <EmptyState icon={<ClockIcon className="size-5" />} title="Job Run History"
           description="Execution history for all cronjob and one-shot job workloads — run time, exit code, duration, triggering actor."
           actionHref="/jobs" actionLabel="Go to Jobs" />
       ) : tab === "logs" ? (
         <EmptyState icon={<ScrollIcon className="size-5" />} title="Job Logs"
           description="Stdout/stderr from completed job runs. Each run's log tail is stored inline; full log available on demand."
           actionHref="/jobs" actionLabel="Go to Jobs" />
       ) : (
         <EmptyState icon={<AlertCircleIcon className="size-5" />} title="Job Failures"
           description="Failed job runs with exit code, error summary, and last log excerpt."
           actionHref="/jobs" actionLabel="Go to Jobs" />
       )}
    </PageShell>
  );
}
