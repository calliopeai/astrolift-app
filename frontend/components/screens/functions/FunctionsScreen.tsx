"use client";

import { BoltIcon } from "lucide-react";
import Link from "next/link";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { FunctionsState } from "./use-functions";

export type FunctionsScreenProps = FunctionsState;

// The row's link is an ::after overlay stretched across the whole row; a
// link in a cell has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

const columns: Column<AstroliftWorkload>[] = [
  {
    id: "name",
    header: "Function",
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
    id: "minScale",
    header: "Min scale",
    align: "right",
    cell: (w) => <span className="font-mono text-xs">{w.hpaMinReplicas ?? 0}</span>,
  },
  {
    id: "maxScale",
    header: "Max scale",
    align: "right",
    cell: (w) =>
      w.hpaMaxReplicas != null ? (
        <span className="font-mono text-xs">{w.hpaMaxReplicas}</span>
      ) : (
        <span className="text-muted-foreground text-xs">none</span>
      ),
  },
];

/**
 * Agents › Functions (spec 44 §4.4, §5.1): event-driven container
 * invocations that scale to zero and trigger on HTTP, queues or webhooks,
 * on the shared list, views All · Mine. Each row opens the workload on its
 * app. Throughput, errors, latency and logs arrive with the function
 * runtime; until then there is nothing to chart. Pure view; the data half
 * is useFunctions.
 */
export function FunctionsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: FunctionsScreenProps) {
  return (
    <ListPage<AstroliftWorkload>
      header={{
        crumbs: agentsCrumbs("functions"),
        title: "Functions",
        context:
          "Scale to zero and trigger on HTTP, queues or webhooks. Throughput, errors, latency and logs arrive with the function runtime.",
      }}
      list={list}
      label="Functions"
      columns={columns}
      rows={rows}
      getRowId={(w) => w.id}
      rowHref={(w) => `/apps/${w.registeredAppSlug}/workloads/${w.slug}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BoltIcon className="size-5" />,
        title: "No function workloads",
        description:
          "Apps with kind: function in their manifest will appear here. Functions scale to zero and trigger on HTTP, queues, or events.",
      }}
      totalCount={totalCount}
    />
  );
}
