"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@apollo/client/react";
import { ClipboardListIcon, ClockIcon, LayersIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

type Tab = "fleet" | "history" | "logs";
const TABS: readonly Tab[] = ["fleet", "history", "logs"];
const TAB_LABELS: Record<Tab, string> = { fleet: "Fleet", history: "Run History", logs: "Logs" };
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  history: <ClockIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
};

function FleetTab() {
  const { data, loading } = useQuery<{ astroliftWorkloads: AstroliftWorkload[] }>(
    LIST_WORKLOADS, { variables: {}, fetchPolicy: "cache-and-network" }
  );
  const workloads = (data?.astroliftWorkloads ?? []).filter((w) => w.kind === "task");

  if (loading && workloads.length === 0)
    return <div className="space-y-2"><Skeleton className="h-12 w-full" /><Skeleton className="h-12 w-full" /></div>;

  if (workloads.length === 0)
    return <EmptyState icon={<ClipboardListIcon className="size-5" />} title="No task workloads"
      description="Apps with kind: task in their manifest will appear here. Tasks are operator-initiated one-off commands."
      actionHref="/tasks" actionLabel="Go to Tasks" />;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Workload</TableHead>
          <TableHead>App</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {workloads.map((w) => (
          <TableRow key={w.id}>
            <TableCell className="font-medium">
              <Link href={`/apps/${w.registeredAppSlug}/workloads`} className="hover:underline">{w.name}</Link>
            </TableCell>
            <TableCell><Badge variant="outline">{w.registeredAppSlug}</Badge></TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function ObserveTasksPage() {
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
    <PageShell title="Observe · Tasks" description="Fleet of one-off task workloads and their execution history.">
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
         <EmptyState icon={<ClockIcon className="size-5" />} title="Task Run History"
           description="Operator-initiated task executions — command run, status, exit code, duration, and triggering user."
           actionHref="/tasks" actionLabel="Go to Tasks" />
       ) : (
         <EmptyState icon={<ScrollIcon className="size-5" />} title="Task Logs"
           description="Stdout/stderr from task run containers. Stored inline for quick inspection."
           actionHref="/tasks" actionLabel="Go to Tasks" />
       )}
    </PageShell>
  );
}
