"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ActivityIcon } from "lucide-react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { PermissionNote } from "@/components/settings/Restricted";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import type { WorkflowInstance } from "@/graphql/workflows/workflows.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { formatInstanceDuration, instanceKey } from "./workflow-instances-list";
import { InstanceStatusBadge } from "./WorkflowInstancesPanel";
import { workflowsCrumbs } from "./workflows-list";

/** The list's data, from useWorkflowInstancesList. */
export interface WorkflowInstancesListProps {
  /** List state in the URL: views, search, chips, page (WORKFLOW_INSTANCES_LIST). */
  list: ListStateController;
  /** The page on screen, already filtered, searched, sorted and sliced. */
  rows: WorkflowInstance[];
  /** Instances matching the view, chips and search, within the one page read. */
  totalCount: number;
  nextCursor?: string | null;
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** The list with `?instance=` set: a row opens its detail beside the list. */
  instanceHref: (instance: WorkflowInstance) => string;
  /** The open instance, from `?instance=`. */
  selectedWorkflowId: string | null;
  selectedRunId?: string | null;
  onCloseInstance: () => void;
}

export type WorkflowInstancesScreenProps =
  /** The permission set is still loading. */
  (
    | { access: "loading" }
    /** Without `audit_log.read`: the permission, and no list mounted. */
    | { access: "denied" }
    | ({
        access: "granted";
        /** InstanceDetailView behind its hook, for the open instance. */
        detail: ReactNode;
      } & WorkflowInstancesListProps)
  ) & { title?: string; crumbs?: import("@/components/shell/ShellHeader").Crumb[] };

const CRUMBS = workflowsCrumbs({ label: "Platform instances" });
const TITLE = "Platform instances";
const CONTEXT = (
  <span className="text-muted-foreground text-sm">
    Temporal operations across apps and platform services
  </span>
);

/**
 * Agents › Workflows › Platform instances (spec 44 §5.1): the Temporal
 * instances behind deploys, provisioning and drift detection, on the shared
 * list with views All · Mine · Running · Failed, Type and Status chips,
 * server cursor pages. A row opens the exact execution beside the list
 * (its activity feed, and cancel and terminate for a stuck one). Reached
 * from the Workflows `⋯` menu, behind `audit_log.read`. Pure.
 */
export function WorkflowInstancesScreen(props: WorkflowInstancesScreenProps) {
  if (props.access !== "granted") {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={props.crumbs ?? CRUMBS}
          title={props.title ?? TITLE}
          context={CONTEXT}
        />
        {props.access === "loading" ? (
          <Skeleton className="h-40 w-full rounded-md" />
        ) : (
          <PermissionNote permission="audit_log.read" verb="Viewing platform instances" />
        )}
      </div>
    );
  }
  return <InstancesList {...props} />;
}

function InstancesList({
  list,
  rows,
  nextCursor,
  loading,
  stale,
  error,
  onRetry,
  instanceHref,
  selectedWorkflowId,
  selectedRunId,
  onCloseInstance,
  detail,
  title = TITLE,
  crumbs = CRUMBS,
}: WorkflowInstancesListProps & {
  detail: ReactNode;
  title?: string;
  crumbs?: import("@/components/shell/ShellHeader").Crumb[];
}) {
  const fmt = useFormatters();
  const t = useTranslations("shared.versionMismatch");

  const columns: Column<WorkflowInstance>[] = [
    {
      id: "instance",
      header: "Instance",
      cellClassName: "max-w-80",
      cell: (i) => (
        <span className="block truncate font-mono text-xs" title={i.workflowId}>
          {i.workflowId}
        </span>
      ),
    },
    {
      id: "type",
      header: "Type",
      cellClassName: "max-w-64",
      cell: (i) => (
        <span className="block truncate text-sm" title={i.workflowType}>
          {i.workflowType}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (i) => <InstanceStatusBadge status={i.status} />,
    },
    {
      id: "started",
      header: "Started",
      cellClassName: "font-mono text-xs whitespace-nowrap",
      cell: (i) => (i.startedAt ? fmt.formatDateTime(i.startedAt) : "not started"),
    },
    {
      id: "duration",
      header: "Took",
      align: "right",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (i) => (i.durationSeconds != null ? formatInstanceDuration(i.durationSeconds) : ""),
    },
    {
      id: "triggeredBy",
      header: "Started by",
      cellClassName: "max-w-48",
      cell: (i) =>
        i.triggeredBy ? (
          <span className="block truncate text-xs" title={i.triggeredBy}>
            {i.triggeredBy}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">platform</span>
        ),
    },
  ];

  return (
    <>
      <ListPage<WorkflowInstance>
        header={{
          crumbs,
          title,
          context: CONTEXT,
          primaryAction: (
            <Button variant="outline" size="sm" onClick={onRetry} disabled={loading || stale}>
              {t("refresh")}
            </Button>
          ),
        }}
        list={list}
        label="Instances"
        columns={columns}
        rows={rows}
        getRowId={instanceKey}
        rowHref={instanceHref}
        rowClassName={(i) =>
          i.workflowId === selectedWorkflowId && i.runId === selectedRunId ? "bg-accent" : undefined
        }
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ActivityIcon className="size-5" />,
          title: "No platform instances",
          description: nextCursor
            ? "No authorized instances on this page. Choose Older to continue through the workflow engine’s pages."
            : "No authorized instances on this page, or the workflow engine is unavailable. Refresh to check again.",
        }}
        nextCursor={nextCursor}
      />
      <Sheet open={selectedWorkflowId !== null} onOpenChange={(open) => !open && onCloseInstance()}>
        <SheetContent
          side="right"
          showCloseButton={false}
          aria-describedby={undefined}
          className="overflow-y-auto p-4 data-[side=right]:w-full data-[side=right]:sm:max-w-xl"
        >
          <SheetTitle className="sr-only">Instance {selectedWorkflowId}</SheetTitle>
          {detail}
        </SheetContent>
      </Sheet>
    </>
  );
}
