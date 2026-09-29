"use client";

import { BoxesIcon } from "lucide-react";
import Link from "next/link";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import { Badge } from "@/components/ui/badge";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { WorkloadsListState } from "./use-workloads-list";
import { KIND_LABEL, scaleLabel } from "./workloads-list";

export type WorkloadsScreenProps = WorkloadsListState;

// The row's link is an ::after overlay stretched across the whole row; a
// link in a cell has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

const columns: Column<AstroliftWorkload>[] = [
  {
    id: "workload",
    header: "Workload",
    sortKey: "name",
    cellClassName: "max-w-80",
    cell: (w) => (
      <span className="block min-w-0">
        <span className="block truncate font-medium" title={w.name}>
          {w.name}
        </span>
        <span className="text-muted-foreground block truncate font-mono text-xs" title={w.slug}>
          {w.slug}
        </span>
      </span>
    ),
  },
  {
    id: "kind",
    header: "Kind",
    sortKey: "kind",
    cell: (w) => <Badge variant="outline">{KIND_LABEL[w.kind] ?? w.kind}</Badge>,
  },
  {
    id: "app",
    header: "App",
    sortKey: "app",
    cellClassName: `${ABOVE_ROW_LINK} max-w-64`,
    cell: (w) => (
      <Link
        href={`/apps/${w.registeredAppSlug}`}
        className="block truncate font-mono text-xs hover:underline"
        title={w.registeredAppSlug}
      >
        {w.registeredAppSlug}
      </Link>
    ),
  },
  {
    id: "scale",
    header: "Replicas",
    align: "right",
    cell: (w) => <span className="font-mono text-xs">{scaleLabel(w)}</span>,
  },
  {
    id: "schedule",
    header: "Schedule",
    cell: (w) =>
      w.schedule ? (
        <span className="font-mono text-xs">{w.schedule}</span>
      ) : (
        <span className="text-muted-foreground text-xs">none</span>
      ),
  },
];

/**
 * Agents › Workloads (spec 44 §4.1, §10.2): the runtime units behind
 * agents, workflows and functions on the shared list, views All · Mine ·
 * Agents · Workflows · Functions. Each row opens the workload on its app.
 * Pure view; the data half is useWorkloadsList.
 */
export function WorkloadsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: WorkloadsScreenProps) {
  return (
    <ListPage<AstroliftWorkload>
      header={{
        crumbs: agentsCrumbs("workloads"),
        title: "Workloads",
        context: "An app's deployments, jobs and cron jobs stay on that app's Workloads tab.",
      }}
      list={list}
      label="Workloads"
      columns={columns}
      rows={rows}
      getRowId={(w) => w.id}
      rowHref={(w) => `/apps/${w.registeredAppSlug}/workloads/${w.slug}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BoxesIcon className="size-5" />,
        title: "No agent, workflow or function workloads",
        description:
          "Apps with kind: agent, workflow or function in their manifest appear here once registered.",
        actionHref: "/agents",
        actionLabel: "Go to agents",
      }}
      totalCount={totalCount}
    />
  );
}
