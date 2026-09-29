"use client";

import { Loader2Icon, PlayIcon, PowerIcon, PowerOffIcon } from "lucide-react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { formatTriggerKind } from "./workflow-run-state";
import type { TriggerRow } from "./workflow-triggers-list";

export interface WorkflowTriggersViewProps {
  /** List state in the URL: views, search, chips, page (WORKFLOW_TRIGGERS_LIST). */
  list: ListStateController;
  /** The page on screen, already filtered, searched, sorted and sliced. */
  rows: TriggerRow[];
  /** Triggers matching the view, chips and search, across all pages. */
  totalCount: number;
  /** `workflow.update`: the enable toggle on each row. */
  canManage: boolean;
  /** The row whose toggle is in flight. */
  togglingId: string | null;
  onToggle: (row: TriggerRow) => void;
}

/**
 * The Triggers tab (spec 44 §5.1, §5.2): the workflow's triggers as an
 * embedded list under the workflow's tabs, each row its kind, schedule and
 * status, with the enable toggle (gated `canManage`). Run itself is the
 * frame's primary action. A definition has no trigger of its own, so its
 * list is the empty state. Pure; the data half is useWorkflowTriggers.
 */
export function WorkflowTriggersView({
  list,
  rows,
  totalCount,
  canManage,
  togglingId,
  onToggle,
}: WorkflowTriggersViewProps) {
  const columns: Column<TriggerRow>[] = [
    {
      id: "kind",
      header: "Trigger",
      sortKey: "kind",
      cellClassName: "max-w-80",
      cell: (t) => (
        <Badge variant="outline" className="max-w-full [overflow-wrap:anywhere] whitespace-normal">
          {formatTriggerKind(t.kind)}
        </Badge>
      ),
    },
    {
      id: "schedule",
      header: "Schedule",
      cellClassName: "max-w-80",
      cell: (t) =>
        t.schedule ? (
          <span className="block font-mono text-xs [overflow-wrap:anywhere]">{t.schedule}</span>
        ) : (
          <span className="text-muted-foreground text-xs">none</span>
        ),
    },
    {
      id: "status",
      header: "Status",
      sortKey: "status",
      cell: (t) => (
        <Badge variant={t.isEnabled ? "default" : "secondary"}>
          {t.isEnabled ? "Enabled" : "Disabled"}
        </Badge>
      ),
    },
  ];

  if (canManage) {
    columns.push({
      id: "toggle",
      header: <span className="sr-only">Enable or disable</span>,
      label: "Enable toggle",
      align: "right",
      cell: (t) => {
        const busy = togglingId === t.id;
        return (
          <Button size="sm" variant="outline" onClick={() => onToggle(t)} disabled={busy}>
            {busy ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : t.isEnabled ? (
              <PowerOffIcon className="size-4" />
            ) : (
              <PowerIcon className="size-4" />
            )}
            {t.isEnabled ? "Disable" : "Enable"}
          </Button>
        );
      },
    });
  }

  return (
    <ListPage<TriggerRow>
      embedded
      list={list}
      label="Triggers"
      columns={columns}
      rows={rows}
      getRowId={(t) => t.id}
      totalCount={totalCount}
      empty={{
        icon: <PlayIcon className="size-5" />,
        title: "Runs on demand",
        description:
          "A workflow definition has no trigger of its own. Start it with Run, or configure a workflow over it to run on a schedule or a webhook.",
        actionHref: "/workflows/new",
        actionLabel: "Configure a workflow",
      }}
    />
  );
}
