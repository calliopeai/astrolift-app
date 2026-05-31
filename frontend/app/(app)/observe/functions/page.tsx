"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@apollo/client/react";
import { AlertCircleIcon, BarChart3Icon, BoltIcon, GaugeIcon, LayersIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { useListControls } from "@/hooks/use-list-controls";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

type Tab = "fleet" | "throughput" | "errors" | "latency" | "logs";
const TABS: readonly Tab[] = ["fleet", "throughput", "errors", "latency", "logs"];
const TAB_LABELS: Record<Tab, string> = {
  fleet: "Fleet",
  throughput: "Throughput",
  errors: "Errors",
  latency: "Latency",
  logs: "Logs",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  throughput: <BarChart3Icon className="size-4" />,
  errors: <AlertCircleIcon className="size-4" />,
  latency: <GaugeIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
};

function FleetTab() {
  const { data, loading } = useQuery<{ astroliftWorkloads: AstroliftWorkload[] }>(
    LIST_WORKLOADS, { variables: {}, fetchPolicy: "cache-and-network" }
  );
  const workloads = (data?.astroliftWorkloads ?? []).filter((w) => w.kind === "function");

  const ctrl = useListControls({
    data: workloads,
    searchFn: (w) => [w.name, w.registeredAppSlug].join(" "),
    sortFn: (key, a, b) => {
      if (key === "name") return a.name.localeCompare(b.name);
      if (key === "app") return a.registeredAppSlug.localeCompare(b.registeredAppSlug);
      return 0;
    },
    initialPageSize: 25,
  });

  if (loading && workloads.length === 0)
    return <div className="space-y-2"><Skeleton className="h-12 w-full" /><Skeleton className="h-12 w-full" /></div>;

  if (workloads.length === 0)
    return <EmptyState icon={<BoltIcon className="size-5" />} title="No function workloads"
      description="Apps with kind: function in their manifest will appear here. Functions scale to zero and trigger on HTTP, queues, or events."
      actionHref="/functions" actionLabel="Go to Functions" />;

  return (
    <>
      <ListControls controls={ctrl} searchPlaceholder="Filter workloads..." />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>
              <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>Workload</SortableHeader>
            </TableHead>
            <TableHead>
              <SortableHeader sortKey="app" sort={ctrl.sort} onToggle={ctrl.toggleSort}>App</SortableHeader>
            </TableHead>
            <TableHead>Min scale</TableHead>
            <TableHead>Max scale</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ctrl.rows.map((w) => (
            <TableRow key={w.id}>
              <TableCell className="font-medium">
                <Link href={`/apps/${w.registeredAppSlug}/workloads`} className="hover:underline">{w.name}</Link>
              </TableCell>
              <TableCell><Badge variant="outline">{w.registeredAppSlug}</Badge></TableCell>
              <TableCell className="text-muted-foreground text-sm">{w.hpaMinReplicas ?? 0}</TableCell>
              <TableCell className="text-muted-foreground text-sm">{w.hpaMaxReplicas ?? "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}

export default function ObserveFunctionsPage() {
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
    <PageShell title="Observe · Functions" description="Fleet of scale-to-zero function workloads and golden signal metrics.">
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
       tab === "throughput" ? (
         <EmptyState icon={<BarChart3Icon className="size-5" />} title="Function Throughput"
           description="Requests per second and invocation count across all function workloads — by trigger source and app." />
       ) : tab === "errors" ? (
         <EmptyState icon={<AlertCircleIcon className="size-5" />} title="Function Errors"
           description="Failed invocations with HTTP status code, error type, and trigger context." />
       ) : tab === "latency" ? (
         <EmptyState icon={<GaugeIcon className="size-5" />} title="Function Latency"
           description="Cold start frequency, warm invocation latency (p50/p95/p99), and end-to-end duration." />
       ) : (
         <EmptyState icon={<ScrollIcon className="size-5" />} title="Function Logs"
           description="Invocation logs from function containers including request context and response code." />
       )}
    </PageShell>
  );
}
