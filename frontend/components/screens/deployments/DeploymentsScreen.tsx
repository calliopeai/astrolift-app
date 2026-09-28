"use client";

import {
  BoxIcon,
  CheckIcon,
  CopyIcon,
  ExternalLinkIcon,
  Loader2Icon,
  PlusIcon,
  RotateCcwIcon,
  StopCircleIcon,
  UndoIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, RowSelection } from "@/components/data-table";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { ListPage } from "@/components/list/ListPage";
import { formatDuration } from "@/components/screens/apps/deployments/app-deployments-format";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { appsListCrumbs } from "./apps-area";
import { IN_FLIGHT, statusToDot, TERMINAL, type ActionKind } from "./deployments-format";
import { deploySha } from "./deployments-list";
import type { useDeployments } from "./use-deployments";

export type DeploymentsScreenProps = ReturnType<typeof useDeployments>;

interface PendingAction {
  kind: ActionKind;
  deployment: AstroliftDeployment;
}

interface BulkAction {
  kind: "abort" | "redeploy";
  deployments: AstroliftDeployment[];
  selection: RowSelection;
}

/** Which of the four lifecycle actions a row offers the viewer. */
function rowActionsFor(
  d: AstroliftDeployment,
  {
    canDeploy,
    canApprove,
    canRollback,
  }: Record<"canDeploy" | "canApprove" | "canRollback", boolean>
): ActionKind[] {
  const inFlight = IN_FLIGHT.has(d.status);
  const out: ActionKind[] = [];
  if (d.status === "pending_approval" && canApprove && !d.triggeredByMe) out.push("approve");
  if (inFlight && canDeploy) out.push("abort");
  if ((d.status === "running" || d.status === "failed") && canRollback) out.push("rollback");
  if (!inFlight && d.status !== "running" && canDeploy) out.push("redeploy");
  return out;
}

const ACTION_ICON: Record<ActionKind, React.ReactNode> = {
  approve: <CheckIcon className="size-4" />,
  abort: <StopCircleIcon className="size-4" />,
  rollback: <UndoIcon className="size-4" />,
  redeploy: <RotateCcwIcon className="size-4" />,
};

/**
 * Apps › Deployments (spec 44 §5.1): every rollout across apps on the
 * shared list. Views All · Mine · Waiting approval · Failed · Today, filters
 * app, environment, status, trigger and since, cursor paged, live behind the
 * "new" pill. Row actions and the bulk bar keep the lifecycle mutations
 * behind their confirms. Pure view; the data half is useDeployments.
 */
export function DeploymentsScreen({
  list,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  deploymentsById,
  canDeploy,
  canApprove,
  canRollback,
  busy,
  bulkRunning,
  runAction,
  runBulk,
  startHref,
}: DeploymentsScreenProps) {
  const t = useTranslations("lists.deployments");
  const fmt = useFormatters();
  const [pendingAction, setPendingAction] = React.useState<PendingAction | null>(null);
  const [pendingBulk, setPendingBulk] = React.useState<BulkAction | null>(null);
  const perms = { canDeploy, canApprove, canRollback };
  const hasAnyAction = canDeploy || canApprove || canRollback;

  const ACTION_COPY: Record<
    Exclude<ActionKind, "abort">,
    {
      title: (d: AstroliftDeployment) => string;
      description: (d: AstroliftDeployment) => string;
      confirmLabel: string;
    }
  > = {
    approve: {
      title: (d) => t("confirm.approveTitle", { tag: d.imageTag }),
      description: (d) =>
        t("confirm.approveDescription", { app: d.registeredAppSlug, env: d.environmentName }),
      confirmLabel: t("confirm.approveConfirm"),
    },
    rollback: {
      title: (d) =>
        t("confirm.rollbackTitle", { app: d.registeredAppSlug, env: d.environmentName }),
      description: () => t("confirm.rollbackDescription"),
      confirmLabel: t("confirm.rollbackConfirm"),
    },
    redeploy: {
      title: (d) =>
        t("confirm.redeployTitle", {
          tag: d.imageTag,
          app: d.registeredAppSlug,
          env: d.environmentName,
        }),
      description: () => t("confirm.redeployDescription"),
      confirmLabel: t("confirm.redeployConfirm"),
    },
  };
  const ACTION_LABEL: Record<ActionKind, string> = {
    approve: t("actions.approve"),
    abort: t("actions.abort"),
    rollback: t("actions.rollback"),
    redeploy: t("actions.redeploy"),
  };

  const columns: Column<AstroliftDeployment>[] = [
    {
      id: "deployment",
      header: t("columns.appEnv"),
      cellClassName: "max-w-80",
      // The status dot folds into the linking cell: a link whose only
      // content is a dot has no name.
      cell: (d) => (
        <span className="flex min-w-0 items-start gap-2">
          <StatusDot status={statusToDot[d.status]} className="mt-1.5 shrink-0" />
          <span className="block min-w-0">
            <span className="block truncate font-medium" title={d.registeredAppSlug}>
              {d.registeredAppSlug}
            </span>
            <span className="text-muted-foreground block truncate text-xs">
              <span className="font-mono">{d.environmentName}</span>
              {d.workloadSlug && (
                <>
                  {" · "}
                  <span className="font-mono">{d.workloadSlug}</span>
                </>
              )}
              {" · "}
              <span className="font-mono">{deploySha(d)}</span>
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "image",
      header: t("columns.image"),
      cellClassName: "max-w-56",
      cell: (d) => (
        <span className="block truncate font-mono text-xs" title={d.imageTag}>
          {d.imageTag || "—"}
        </span>
      ),
    },
    {
      id: "trigger",
      header: t("columns.trigger"),
      cell: (d) => (
        <Badge variant="outline" className="font-mono">
          {d.triggerKind}
        </Badge>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (d) => (
        <>
          <DeploymentStatusPill status={d.status} label={t(`statusOptions.${d.status}`)} />
          {d.approvalsRequired > 0 && (
            <span className="text-muted-foreground mt-1 block font-mono text-xs">
              {t("approvalsCount", {
                received: d.approvalsReceived,
                required: d.approvalsRequired,
              })}
            </span>
          )}
        </>
      ),
    },
    {
      id: "duration",
      header: t("columns.duration"),
      cell: (d) => (
        <span className="font-mono text-xs tabular-nums">{formatDuration(d.durationSeconds)}</span>
      ),
    },
    {
      id: "started",
      header: t("columns.started"),
      cell: (d) => (
        <span className="text-muted-foreground font-mono text-xs">
          {fmt.formatDateTime(d.startedAt ?? d.createdAt)}
        </span>
      ),
    },
  ];

  function rowActions(d: AstroliftDeployment) {
    const kinds = rowActionsFor(d, perms);
    return (
      <>
        {kinds.map((kind) => (
          <DropdownMenuItem
            key={kind}
            variant={kind === "abort" ? "destructive" : undefined}
            disabled={busy || bulkRunning}
            onSelect={() => setPendingAction({ kind, deployment: d })}
          >
            {ACTION_ICON[kind]}
            {ACTION_LABEL[kind]}
          </DropdownMenuItem>
        ))}
        {d.repoUrl && d.commitSha && (
          <DropdownMenuItem asChild>
            <a
              href={`${d.repoUrl.replace(/\/$/, "")}/commit/${d.commitSha}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLinkIcon className="size-4" />
              {t("actions.viewCommit")}
            </a>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          onSelect={() => {
            navigator.clipboard
              .writeText(d.id)
              .then(() => toast.success(t("actions.copyOk")))
              .catch(() => toast.error(t("actions.copyFail")));
          }}
        >
          <CopyIcon className="size-4" />
          {t("actions.copyGuid")}
        </DropdownMenuItem>
      </>
    );
  }

  function bulkActions(selection: RowSelection) {
    const picked = selection.selectedIds
      .map((id) => deploymentsById.get(id))
      .filter((d): d is AstroliftDeployment => Boolean(d));
    const allInFlight = picked.length > 0 && picked.every((d) => IN_FLIGHT.has(d.status));
    const allTerminal = picked.length > 0 && picked.every((d) => TERMINAL.has(d.status));
    // Neither action is valid for a mixed set, so both say why they are off.
    const mixed = picked.length > 0 && !allInFlight && !allTerminal;
    const envs = summarize(picked);
    return (
      <>
        <span
          className={
            mixed
              ? "text-danger-fg min-w-0 text-xs"
              : "text-muted-foreground min-w-0 text-xs [overflow-wrap:anywhere]"
          }
        >
          {mixed ? t("bulk.mixedWarning") : t("bulk.summary", { envs })}
        </span>
        <Button
          size="sm"
          variant="destructive"
          disabled={!allInFlight || bulkRunning || !canDeploy}
          onClick={() => setPendingBulk({ kind: "abort", deployments: picked, selection })}
        >
          {bulkRunning ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <StopCircleIcon className="size-4" />
          )}
          {t("bulk.cancelButton", { count: picked.length })}
        </Button>
        <Button
          size="sm"
          disabled={!allTerminal || bulkRunning || !canDeploy}
          onClick={() => setPendingBulk({ kind: "redeploy", deployments: picked, selection })}
        >
          {bulkRunning ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <RotateCcwIcon className="size-4" />
          )}
          {t("bulk.redeployButton", { count: picked.length })}
        </Button>
      </>
    );
  }

  const confirmable =
    pendingAction && pendingAction.kind !== "abort"
      ? { kind: pendingAction.kind, deployment: pendingAction.deployment }
      : null;
  const bulkEnvs = pendingBulk ? summarize(pendingBulk.deployments) : "";

  return (
    <>
      <ListPage<AstroliftDeployment>
        header={{
          crumbs: appsListCrumbs("deployments"),
          title: t("title"),
          primaryAction: (
            <Can permission="app.deploy">
              <Button size="sm" asChild>
                <Link href={startHref}>
                  <PlusIcon className="size-4" />
                  {t("start")}
                </Link>
              </Button>
            </Can>
          ),
        }}
        list={list}
        label="Deployments"
        columns={columns}
        rows={rows}
        getRowId={(d) => d.id}
        rowHref={(d) => `/deployments/${d.id}`}
        rowActions={rowActions}
        // Selection drives the bulk bar, which offers nothing to a viewer
        // who holds none of the deploy permissions.
        bulkActions={hasAnyAction ? bulkActions : undefined}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <BoxIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          actionHref: "/apps",
          actionLabel: t("openApps"),
        }}
        totalCount={totalCount}
        nextCursor={nextCursor}
        newRows={newRows}
      />

      <ConfirmDialog
        open={confirmable !== null}
        onOpenChange={(next) => {
          if (!next) setPendingAction(null);
        }}
        title={confirmable ? ACTION_COPY[confirmable.kind].title(confirmable.deployment) : ""}
        description={
          confirmable ? ACTION_COPY[confirmable.kind].description(confirmable.deployment) : ""
        }
        confirmLabel={confirmable ? ACTION_COPY[confirmable.kind].confirmLabel : "Confirm"}
        onConfirm={async () => {
          if (confirmable) await runAction(confirmable.kind, confirmable.deployment);
        }}
      />

      <ConfirmDialog
        reason={{
          label: t("confirm.abortReasonLabel"),
          placeholder: t("confirm.abortReasonPlaceholder"),
          requiredError: t("confirm.reasonRequired"),
        }}
        open={pendingAction?.kind === "abort"}
        onOpenChange={(next) => {
          if (!next) setPendingAction(null);
        }}
        title={
          pendingAction?.kind === "abort"
            ? t("confirm.abortTitle", {
                app: pendingAction.deployment.registeredAppSlug,
                env: pendingAction.deployment.environmentName,
              })
            : ""
        }
        description={t("confirm.abortDescription")}
        confirmLabel={t("confirm.abortConfirm")}
        destructive
        onConfirm={async (reason) => {
          if (pendingAction?.kind === "abort") {
            await runAction("abort", pendingAction.deployment, reason);
          }
        }}
      />

      {/* Bulk cancel needs a reason, the same backend boundary as a row's abort. */}
      <ConfirmDialog
        reason={{
          label: t("bulk.confirmCancel.reasonLabel"),
          placeholder: t("bulk.confirmCancel.reasonPlaceholder"),
          requiredError: t("confirm.reasonRequired"),
        }}
        open={pendingBulk?.kind === "abort"}
        onOpenChange={(next) => {
          if (!next) setPendingBulk(null);
        }}
        title={
          pendingBulk?.kind === "abort"
            ? t("bulk.confirmCancel.title", { count: pendingBulk.deployments.length })
            : ""
        }
        description={t("bulk.confirmCancel.description", { envs: bulkEnvs })}
        confirmLabel={t("bulk.confirmCancel.confirm")}
        destructive
        onConfirm={async (reason) => {
          if (pendingBulk?.kind === "abort") {
            await runBulk("abort", pendingBulk.deployments, reason);
            pendingBulk.selection.clear();
          }
        }}
      />

      <ConfirmDialog
        open={pendingBulk?.kind === "redeploy"}
        onOpenChange={(next) => {
          if (!next) setPendingBulk(null);
        }}
        title={
          pendingBulk?.kind === "redeploy"
            ? t("bulk.confirmRedeploy.title", { count: pendingBulk.deployments.length })
            : ""
        }
        description={t("bulk.confirmRedeploy.description", { envs: bulkEnvs })}
        confirmLabel={t("bulk.confirmRedeploy.confirm")}
        onConfirm={async () => {
          if (pendingBulk?.kind === "redeploy") {
            await runBulk("redeploy", pendingBulk.deployments);
            pendingBulk.selection.clear();
          }
        }}
      />
    </>
  );
}

function summarize(deployments: AstroliftDeployment[]): string {
  return Array.from(
    new Set(deployments.map((d) => `${d.registeredAppSlug}/${d.environmentName}`))
  ).join(", ");
}
