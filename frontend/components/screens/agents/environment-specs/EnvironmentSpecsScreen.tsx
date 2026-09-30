"use client";

import { BoxesIcon } from "lucide-react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { useFormatters } from "@/lib/i18n/formatters";

import { agentsCrumbs } from "../skills/catalog";
import { type EnvironmentSpec, specOwner } from "./environment-specs";

export interface EnvironmentSpecsScreenProps {
  list: ListStateController;
  rows: EnvironmentSpec[];
  totalCount: number;
  loading: boolean;
  stale?: boolean;
  error: { message: string } | null;
  onRetry: () => void;
}

export function EnvironmentSpecsScreen(props: EnvironmentSpecsScreenProps) {
  const fmt = useFormatters();
  const columns: Column<EnvironmentSpec>[] = [
    {
      id: "name",
      header: "Environment spec",
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (s) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={s.name}>
            {s.name}
          </span>
          <span className="text-muted-foreground block truncate font-mono text-xs" title={s.slug}>
            {s.slug}
          </span>
        </span>
      ),
    },
    {
      id: "runtime",
      header: "Runtime / image",
      sortKey: "runtime",
      cellClassName: "max-w-96",
      cell: (s) => (
        <span className="block truncate font-mono text-xs" title={s.imageTag || s.runtime}>
          {s.imageTag || s.runtime || "Workload default"}
        </span>
      ),
    },
    { id: "agentType", header: "Agent type", cell: (s) => s.agentType },
    { id: "owner", header: "Owner", cell: specOwner },
    {
      id: "updated",
      header: "Updated",
      sortKey: "updated",
      cell: (s) => (
        <span className="text-muted-foreground font-mono text-xs">
          {fmt.formatDateTime(s.updatedAt)}
        </span>
      ),
    },
  ];
  return (
    <ListPage<EnvironmentSpec>
      {...props}
      header={{
        crumbs: agentsCrumbs("environment-specs"),
        title: "Environment specs",
        context: "Reusable container recipes available to your agents in this organization.",
      }}
      label="Environment specs"
      columns={columns}
      getRowId={(s) => s.id}
      rowHref={(s) => `/agents/environment-specs/${encodeURIComponent(s.slug)}`}
      empty={{
        icon: <BoxesIcon className="size-5" />,
        title: "No environment specs",
        description: "Recipes appear here when agents are registered with a container environment.",
      }}
    />
  );
}
