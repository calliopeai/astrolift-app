"use client";

import { GitCompareIcon, RocketIcon } from "lucide-react";
import * as React from "react";

import type { Column, RowSelection } from "@/components/data-table";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { Identifier } from "@/components/Identifier";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/use-list-state";
import { Button } from "@/components/ui/button";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { formatDuration } from "./app-deployments-format";
import { deploymentHref } from "./app-deployments-list";
import type { ActionTarget } from "./DeploymentRowActions";

export interface CompareSlotArgs {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  deployA: AstroliftDeployment;
  deployB: AstroliftDeployment;
}

export interface AppDeploymentsScreenProps {
  /** List state: view, chips, search and cursor (see app-deployments-list.ts). */
  list: ListStateController;
  /** The page of deployments on screen, newest first. */
  rows: AstroliftDeployment[];
  /** Rows that arrived while the reader was on the first page. */
  newRows?: { count: number; onReveal: () => void };
  /** First load, with no rows yet. */
  loading: boolean;
  /** A new question is on the way; the rows on screen answer the last one. */
  stale?: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  totalCount: number | null;
  /** A deployment seen on any page, for a compare across pages. */
  lookup: (id: string) => AstroliftDeployment | null;
  /** Where a first deployment starts: the app's environments. */
  environmentsHref: string;
  /** The deployment's run page. Defaults to `/deployments/<id>`. */
  rowHref?: (deployment: AstroliftDeployment) => string;
  /** The approvals waiting on the viewer, as a panel above the list. */
  approvals?: React.ReactNode;
  /** A row's `⋯` items; `request` opens a confirm dialog outside the menu. */
  renderRowActions?: (
    deployment: AstroliftDeployment,
    request: (target: ActionTarget) => void
  ) => React.ReactNode;
  /** The confirm dialog for the requested action, rendered once for the list. */
  renderActionDialog?: (target: ActionTarget | null, onClose: () => void) => React.ReactNode;
  /** The compare sheet for exactly two selected deployments. */
  renderCompare?: (args: CompareSlotArgs) => React.ReactNode;
}

/**
 * The status cell: the pill, and why a deploy failed or what it waits on,
 * without opening it (#2123).
 */
export function DeploymentStatusCell({ deployment: d }: { deployment: AstroliftDeployment }) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      <DeploymentStatusPill status={d.status} />
      {d.statusReason && (
        <p
          className="text-muted-foreground text-2xs line-clamp-2 [overflow-wrap:anywhere]"
          title={d.statusReason}
        >
          {d.statusReason}
        </p>
      )}
    </div>
  );
}

/**
 * An app's Deployments tab (spec 44 §4.4, §5.1): the app's deployments on
 * the embedded list, cursor paged and live behind the new-rows pill. Views
 * are a picker in the filter bar (All · Mine · Waiting approval · Failed ·
 * Today · Previews). The whole row opens the deployment's run page; its
 * actions sit in `⋯`; two selected rows compare. Pure.
 */
export function AppDeploymentsScreen({
  list,
  rows,
  newRows,
  loading,
  stale = false,
  error,
  onRetry,
  nextCursor,
  totalCount,
  lookup,
  environmentsHref,
  rowHref = (d) => deploymentHref(d.id),
  approvals,
  renderRowActions,
  renderActionDialog,
  renderCompare,
}: AppDeploymentsScreenProps) {
  const fmt = useFormatters();
  const [target, setTarget] = React.useState<ActionTarget | null>(null);
  const [pair, setPair] = React.useState<[AstroliftDeployment, AstroliftDeployment] | null>(null);

  const columns: Column<AstroliftDeployment>[] = [
    {
      id: "deployment",
      header: "Deployment",
      cellClassName: "max-w-64",
      cell: (d) => (
        <div className="flex min-w-0 flex-col">
          {d.imageTag ? (
            <span className="truncate font-mono text-xs font-medium" title={d.imageTag}>
              {d.imageTag}
            </span>
          ) : (
            <Identifier value={d.id} kind="id" copyable={false} className="text-xs" />
          )}
          {d.workloadSlug && (
            <span
              className="text-muted-foreground text-2xs truncate font-mono"
              title={d.workloadSlug}
            >
              {d.workloadSlug}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      cellClassName: "max-w-64",
      cell: (d) => <DeploymentStatusCell deployment={d} />,
    },
    {
      id: "env",
      header: "Environment",
      cellClassName: "max-w-48",
      cell: (d) => (
        <span className="block truncate font-mono text-xs" title={d.environmentName}>
          {d.environmentName}
        </span>
      ),
    },
    {
      id: "commit",
      header: "Commit",
      cellClassName: "max-w-72",
      cell: (d) =>
        d.commitSha ? (
          <div className="flex min-w-0 flex-col">
            <span className="flex min-w-0 items-center gap-1.5 font-mono text-xs">
              <Identifier value={d.commitSha} kind="sha" copyable={false} />
              {d.branch && (
                <span className="text-muted-foreground truncate" title={d.branch}>
                  {d.branch}
                </span>
              )}
            </span>
            {d.commitMessage && (
              <span className="text-muted-foreground truncate text-xs" title={d.commitMessage}>
                {d.commitMessage}
              </span>
            )}
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        ),
    },
    {
      id: "trigger",
      header: "Trigger",
      cellClassName: "max-w-48",
      cell: (d) => (
        <div className="flex min-w-0 flex-col font-mono text-xs">
          <span>{d.triggerKind}</span>
          {d.commitAuthor && (
            <span className="text-muted-foreground truncate" title={d.commitAuthor}>
              {d.commitAuthor}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "started",
      header: "Started",
      cellClassName: "font-mono text-xs whitespace-nowrap",
      cell: (d) => fmt.formatDateTime(d.startedAt ?? d.createdAt),
    },
    {
      id: "took",
      header: "Took",
      align: "right",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (d) => formatDuration(d.durationSeconds),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      {approvals}

      <ListPage<AstroliftDeployment>
        embedded
        list={list}
        label="Deployments"
        columns={columns}
        rows={rows}
        getRowId={(d) => d.id}
        rowHref={rowHref}
        rowActions={renderRowActions ? (d) => renderRowActions(d, setTarget) : undefined}
        bulkActions={
          renderCompare
            ? (selection) => (
                <CompareAction selection={selection} lookup={lookup} onCompare={setPair} />
              )
            : undefined
        }
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <RocketIcon className="size-5" />,
          title: "No deployments yet",
          description:
            "This app hasn't been deployed yet. Deploy from the header, or start one from an environment.",
          actionHref: environmentsHref,
          actionLabel: "Start a deployment",
          learnMoreHref: "/documentation",
        }}
        totalCount={totalCount}
        nextCursor={nextCursor}
        newRows={newRows}
      />

      {renderActionDialog?.(target, () => setTarget(null))}

      {pair &&
        renderCompare?.({
          open: true,
          onOpenChange: (open) => {
            if (!open) setPair(null);
          },
          deployA: pair[0],
          deployB: pair[1],
        })}
    </div>
  );
}

/** The bulk action: Compare, with exactly two selected, oldest first so the diff reads A to B. */
function CompareAction({
  selection,
  lookup,
  onCompare,
}: {
  selection: RowSelection;
  lookup: (id: string) => AstroliftDeployment | null;
  onCompare: (pair: [AstroliftDeployment, AstroliftDeployment]) => void;
}) {
  const picked = selection.selectedIds
    .map(lookup)
    .filter((d): d is AstroliftDeployment => d !== null);
  const ready = selection.selectedCount === 2 && picked.length === 2;
  const at = (d: AstroliftDeployment) => Date.parse(d.startedAt ?? d.createdAt);
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={!ready}
      onClick={() => {
        if (!ready) return;
        const [a, b] = picked;
        onCompare(at(a) <= at(b) ? [a, b] : [b, a]);
      }}
    >
      <GitCompareIcon className="size-3.5" />
      {ready ? "Compare" : "Compare (select exactly 2)"}
    </Button>
  );
}
