"use client";

import { useQuery } from "@apollo/client/react";
import { AlertCircleIcon, BarChart3Icon, BoltIcon, GaugeIcon, LayersIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { useListControls } from "@/hooks/use-list-controls";

// Tabs folded in from /observe/functions (#892). Fleet is the query-backed
// workload list; the four signal tabs are gateway placeholders — the
// function runtime hasn't shipped, so there are no metrics to back them yet.
type FunctionTab = "fleet" | "throughput" | "errors" | "latency" | "logs";
const FUNCTION_TABS: readonly FunctionTab[] = ["fleet", "throughput", "errors", "latency", "logs"];

const TAB_LABELS: Record<FunctionTab, string> = {
  fleet: "Fleet",
  throughput: "Throughput",
  errors: "Errors",
  latency: "Latency",
  logs: "Logs",
};

const TAB_ICONS: Record<FunctionTab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  throughput: <BarChart3Icon className="size-4" />,
  errors: <AlertCircleIcon className="size-4" />,
  latency: <GaugeIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
};

function FleetTab() {
  const { data, loading } = useQuery<{ astroliftWorkloads: AstroliftWorkload[] }>(LIST_WORKLOADS, {
    variables: {},
    fetchPolicy: "cache-and-network",
  });
  const workloads = (data?.astroliftWorkloads ?? []).filter((w) => w.kind === "function");

  const ctrl = useListControls({
    data: workloads,
    searchFn: (w) => [w.name, w.registeredAppSlug].join(" "),
    sortFn: (a, b, sort) => {
      if (sort.key === "name") return a.name.localeCompare(b.name);
      if (sort.key === "app") return a.registeredAppSlug.localeCompare(b.registeredAppSlug);
      return 0;
    },
    initialPageSize: 25,
  });

  if (loading && workloads.length === 0) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (workloads.length === 0) {
    return (
      <EmptyState
        icon={<BoltIcon className="size-5" />}
        title="No function workloads"
        description="Apps with kind: function in their manifest will appear here. Functions scale to zero and trigger on HTTP, queues, or events."
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
              <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                Workload
              </SortableHeader>
            </TableHead>
            <TableHead>
              <SortableHeader sortKey="app" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                App
              </SortableHeader>
            </TableHead>
            <TableHead>Min scale</TableHead>
            <TableHead>Max scale</TableHead>
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
              <TableCell className="text-muted-foreground text-sm">{w.hpaMinReplicas ?? 0}</TableCell>
              <TableCell className="text-muted-foreground text-sm">{w.hpaMaxReplicas ?? "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}

export function FunctionsClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as FunctionTab | null;
  const tab: FunctionTab = rawTab && FUNCTION_TABS.includes(rawTab) ? rawTab : "fleet";

  function setTab(next: FunctionTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "fleet") {
      params.delete("tab");
    } else {
      params.set("tab", next);
    }
    const qs = params.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Functions"
      description="Event-driven container invocations — scale to zero, trigger on HTTP, queues, or webhooks. The runtime layer hasn't shipped yet; these signal surfaces activate once it does."
    >
      {/* Tab strip — URL-synced via ?tab= param. "fleet" is the default
          and omitted from the URL to keep the canonical /functions link clean. */}
      <div
        role="tablist"
        aria-label="Function signal tabs"
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {FUNCTION_TABS.map((tabKey) => {
          const active = tab === tabKey;
          return (
            <button
              key={tabKey}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(tabKey)}
              className={
                "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition " +
                (active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {TAB_ICONS[tabKey]}
              {TAB_LABELS[tabKey]}
            </button>
          );
        })}
      </div>

      {tab === "fleet" && <FleetTab />}
      {tab === "throughput" && (
        <EmptyState
          icon={<BarChart3Icon className="size-5" />}
          title="Function Throughput"
          description="Requests per second and invocation count across all function workloads — by trigger source and app."
        />
      )}
      {tab === "errors" && (
        <EmptyState
          icon={<AlertCircleIcon className="size-5" />}
          title="Function Errors"
          description="Failed invocations with HTTP status code, error type, and trigger context."
        />
      )}
      {tab === "latency" && (
        <EmptyState
          icon={<GaugeIcon className="size-5" />}
          title="Function Latency"
          description="Cold start frequency, warm invocation latency (p50/p95/p99), and end-to-end duration."
        />
      )}
      {tab === "logs" && (
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title="Function Logs"
          description="Invocation logs from function containers including request context and response code."
        />
      )}
    </PageShell>
  );
}
