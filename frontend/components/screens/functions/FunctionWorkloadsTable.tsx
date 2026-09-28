"use client";

import { BoltIcon } from "lucide-react";

import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { useFunctionWorkloads } from "./use-functions";

export type FunctionWorkloadsTableProps = ReturnType<typeof useFunctionWorkloads>;

const COLUMNS: Column<AstroliftWorkload>[] = [
  {
    id: "name",
    header: "Workload",
    cell: (w) => <span className="font-medium">{w.name}</span>,
  },
  {
    id: "app",
    header: "App",
    cell: (w) => <Badge variant="outline">{w.registeredAppSlug}</Badge>,
  },
  {
    id: "minScale",
    header: "Min scale",
    cellClassName: "text-muted-foreground text-sm",
    cell: (w) => w.hpaMinReplicas ?? 0,
  },
  {
    id: "maxScale",
    header: "Max scale",
    cellClassName: "text-muted-foreground text-sm",
    cell: (w) => w.hpaMaxReplicas ?? "—",
  },
];

/** The Functions "Fleet" tab: function-kind workloads across the org. */
export function FunctionWorkloadsTable({ table }: FunctionWorkloadsTableProps) {
  return (
    <DataTable
      label="Function workloads"
      controller={table}
      columns={COLUMNS}
      getRowId={(w) => w.id}
      rowHref={(w) => `/apps/${w.registeredAppSlug}/workloads/${w.slug}`}
      searchPlaceholder="Filter workloads..."
      empty={{
        icon: <BoltIcon className="size-5" />,
        title: "No function workloads",
        description:
          "Apps with kind: function in their manifest will appear here. Functions scale to zero and trigger on HTTP, queues, or events.",
      }}
      emptyFiltered={{
        title: "No matching function workloads",
        description:
          "No function workload on this page matches that search. The server matches workload name, slug and kind plus the owning app's slug.",
      }}
    />
  );
}
