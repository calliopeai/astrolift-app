"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@apollo/client/react";
import {
  BarChart3Icon,
  GitBranchIcon,
  LayersIcon,
  RocketIcon,
  ScrollIcon,
} from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { useListControls } from "@/hooks/use-list-controls";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

// ---------------------------------------------------------------------------
// Tabs: Fleet first, then signal surfaces
// ---------------------------------------------------------------------------

type Tab = "fleet" | "metrics" | "logs" | "traces";
const TABS: readonly Tab[] = ["fleet", "metrics", "logs", "traces"];
const TAB_LABELS: Record<Tab, string> = {
  fleet: "Fleet",
  metrics: "Metrics",
  logs: "Logs",
  traces: "Traces",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  metrics: <BarChart3Icon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  traces: <GitBranchIcon className="size-4" />,
};

// ---------------------------------------------------------------------------
// Fleet tab — list of all deployment workloads across all apps
// ---------------------------------------------------------------------------

function FleetTab() {
  const { data, loading } = useQuery<{ astroliftWorkloads: AstroliftWorkload[] }>(LIST_WORKLOADS, {
    variables: {},
    fetchPolicy: "cache-and-network",
  });
  const workloads = (data?.astroliftWorkloads ?? []).filter((w) => w.kind === "deployment");

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

  if (loading && workloads.length === 0) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (workloads.length === 0) {
    return (
      <EmptyState
        icon={<RocketIcon className="size-5" />}
        title="No deployment workloads"
        description="Apps with kind: deployment in their manifest will appear here once registered."
        actionHref="/apps"
        actionLabel="Browse apps"
      />
    );
  }

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
            <TableHead>Replicas</TableHead>
            <TableHead>Public</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ctrl.rows.map((w) => (
            <TableRow key={w.id}>
              <TableCell className="font-medium">
                <Link href={`/apps/${w.registeredAppSlug}/workloads`} className="hover:underline">
                  {w.name}
                </Link>
              </TableCell>
              <TableCell>
                <Badge variant="outline">{w.registeredAppSlug}</Badge>
              </TableCell>
              <TableCell className="text-muted-foreground text-sm">{w.replicas}</TableCell>
              <TableCell>
                {w.isPublic && <Badge variant="default" className="text-xs">public</Badge>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}

function GatewayTab({ tab }: { tab: Tab }) {
  const descriptions: Record<string, string> = {
    metrics:
      "Rollout success rate, request latency (p50/p95/p99), error rate, and pod restart counts across all deployment workloads.",
    logs:
      "Fleet-wide log search across all deployment container stdout/stderr. Filter by app, workload, severity, or time range.",
    traces:
      "Distributed trace explorer for deployment workloads — latency, downstream errors, and service dependencies.",
  };
  return (
    <EmptyState
      icon={TAB_ICONS[tab]}
      title={`Deployment ${TAB_LABELS[tab]}`}
      description={descriptions[tab]}
      actionHref={tab === "logs" ? "/logs" : tab === "traces" ? "/traces" : "/metrics"}
      actionLabel={`Open ${TAB_LABELS[tab]} explorer`}
    />
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ObserveDeploymentsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as Tab | null;
  const tab: Tab = rawTab && TABS.includes(rawTab) ? rawTab : "fleet";

  function setTab(next: Tab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "fleet") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Observe · Deployments"
      description="Fleet view of all running deployments, plus health signals and logs."
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

      {tab === "fleet" ? <FleetTab /> : <GatewayTab tab={tab} />}
    </PageShell>
  );
}
