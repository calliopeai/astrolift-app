"use client";

import {
  BarChart3Icon,
  BoxIcon,
  CheckIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitBranchIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  PlusIcon,
  RotateCcwIcon,
  ScrollIcon,
  StopCircleIcon,
  UndoIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable } from "@/components/data-table";
import type { Column } from "@/components/data-table";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { formatDuration } from "@/components/screens/apps/deployments/app-deployments-format";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import {
  DEPLOYMENT_TABS,
  IN_FLIGHT,
  signalTab,
  statusToDot,
  TERMINAL,
  type ActionKind,
  type FleetTab,
  type SignalTab,
} from "./deployments-format";
import type { useDeployments } from "./use-deployments";

export interface StartDialogSlotProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export type DeploymentsScreenProps = ReturnType<typeof useDeployments> & {
  /** The start-deployment sheet, a container so its queries run only while it is open. */
  renderStartDialog: (props: StartDialogSlotProps) => React.ReactNode;
};

const SIGNAL_COPY: Record<
  SignalTab,
  { label: string; icon: React.ReactNode; description: string; actionHref: string }
> = {
  metrics: {
    label: "Metrics",
    icon: <BarChart3Icon className="size-5" />,
    description:
      "Rollout success rate, request latency (p50/p95/p99), error rate, and pod restart counts across all deployment workloads.",
    actionHref: "/administration/metrics",
  },
  logs: {
    label: "Logs",
    icon: <ScrollIcon className="size-5" />,
    description:
      "Fleet-wide log search across all deployment container stdout/stderr. Filter by app, workload, severity, or time range.",
    actionHref: "/logs",
  },
  traces: {
    label: "Traces",
    icon: <GitBranchIcon className="size-5" />,
    description:
      "Distributed trace explorer for deployment workloads — latency, downstream errors, and service dependencies.",
    actionHref: "/traces",
  },
};

// DataTable stretches the row's link across the whole row (an ::after on
// the first cell), and that overlay paints above the un-positioned cells
// beside it. The selection checkbox and the action buttons have to be
// lifted back on top of it or the only thing a click in those cells can
// do is navigate.
const INTERACTIVE_CELLS =
  "[&>td:has([role=checkbox])]:relative [&>td:has([role=checkbox])]:z-10 " +
  "[&>td:last-child]:relative [&>td:last-child]:z-10";

interface PendingAction {
  kind: ActionKind;
  deployment: AstroliftDeployment;
}

interface BulkAction {
  kind: "abort" | "redeploy";
  deployments: AstroliftDeployment[];
}

/** The fleet deployments list: tabs, the paged table, bulk actions and their confirms. */
export function DeploymentsScreen({
  tab,
  setTab,
  table,
  tabCounts,
  selection,
  selectedDeploys,
  canDeploy,
  canApprove,
  canRollback,
  hasAnyAction,
  busy,
  bulkRunning,
  runAction,
  runBulk,
  renderStartDialog,
}: DeploymentsScreenProps) {
  const t = useTranslations("lists.deployments");
  const [openCreate, setOpenCreate] = React.useState(false);
  const signal = signalTab(tab);

  const [pendingAction, setPendingAction] = React.useState<PendingAction | null>(null);
  const [pendingBulk, setPendingBulk] = React.useState<BulkAction | null>(null);

  const ACTION_COPY: Record<
    ActionKind,
    {
      title: (d: AstroliftDeployment) => string;
      description: (d: AstroliftDeployment) => string;
      confirmLabel: string;
      destructive: boolean;
    }
  > = {
    approve: {
      title: (d) => t("confirm.approveTitle", { tag: d.imageTag }),
      description: (d) =>
        t("confirm.approveDescription", {
          app: d.registeredAppSlug,
          env: d.environmentName,
        }),
      confirmLabel: t("confirm.approveConfirm"),
      destructive: false,
    },
    abort: {
      title: (d) => t("confirm.abortTitle", { app: d.registeredAppSlug, env: d.environmentName }),
      description: () => t("confirm.abortDescription"),
      confirmLabel: t("confirm.abortConfirm"),
      destructive: true,
    },
    rollback: {
      title: (d) =>
        t("confirm.rollbackTitle", {
          app: d.registeredAppSlug,
          env: d.environmentName,
        }),
      description: () => t("confirm.rollbackDescription"),
      confirmLabel: t("confirm.rollbackConfirm"),
      destructive: false,
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
      destructive: false,
    },
  };

  const allInFlight =
    selectedDeploys.length > 0 && selectedDeploys.every((d) => IN_FLIGHT.has(d.status));
  const allTerminal =
    selectedDeploys.length > 0 && selectedDeploys.every((d) => TERMINAL.has(d.status));
  const mixedSelection = selectedDeploys.length > 0 && !allInFlight && !allTerminal;

  // Per #420's "explain why the CTA is disabled" pattern: a mixed
  // selection (some in-flight + some terminal) disables both bulk
  // CTAs with an inline hint, since neither action is valid for the
  // whole set.
  const summaryEnvs = Array.from(
    new Set(selectedDeploys.map((d) => `${d.registeredAppSlug}/${d.environmentName}`))
  ).join(", ");

  // No sort controls: `astroliftDeploymentsPage` has no sort argument
  // (its seek key is `-created_at, -guid`), and sorting the page in hand
  // while the rest of the result set sits on the server is wrong at
  // every page boundary.
  const columns: Column<AstroliftDeployment>[] = [
    {
      id: "app",
      header: t("columns.appEnv"),
      cell: (d) => (
        <span className="flex items-start gap-2">
          <StatusDot status={statusToDot[d.status]} className="mt-1.5 shrink-0" />
          <span className="block">
            <span className="block font-medium">{d.registeredAppSlug}</span>
            <span className="text-muted-foreground block text-xs">
              env <span className="font-mono">{d.environmentName}</span>
              {d.workloadSlug && (
                <>
                  {" "}
                  · workload <span className="font-mono">{d.workloadSlug}</span>
                </>
              )}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "image",
      header: t("columns.image"),
      cellClassName: "font-mono text-xs",
      cell: (d) => d.imageTag || "—",
    },
    {
      id: "trigger",
      header: t("columns.trigger"),
      cell: (d) => <Badge variant="outline">{d.triggerKind}</Badge>,
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (d) => (
        <>
          <DeploymentStatusPill status={d.status} label={t(`statusOptions.${d.status}`)} />
          {d.approvalsRequired > 0 && (
            <div className="text-muted-foreground mt-1 text-xs">
              {t("approvalsCount", {
                received: d.approvalsReceived,
                required: d.approvalsRequired,
              })}
            </div>
          )}
        </>
      ),
    },
    {
      id: "duration",
      header: t("columns.duration"),
      cellClassName: "font-mono text-xs",
      cell: (d) => (
        <span className="inline-flex items-center gap-1">
          <ClockIcon className="size-3" />
          {formatDuration(d.durationSeconds)}
        </span>
      ),
    },
    {
      id: "started",
      header: t("columns.started"),
      cellClassName: "text-muted-foreground text-sm",
      cell: (d) =>
        d.startedAt
          ? new Date(d.startedAt).toLocaleString()
          : new Date(d.createdAt).toLocaleString(),
    },
    {
      id: "actions",
      header: t("columns.actions"),
      width: "w-44",
      align: "right",
      cell: (d) => (
        <FleetDeploymentRowActions
          deployment={d}
          canDeploy={canDeploy}
          canApprove={canApprove}
          canRollback={canRollback}
          busy={busy || bulkRunning}
          onAction={(kind) => setPendingAction({ kind, deployment: d })}
        />
      ),
    },
  ];

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      actions={
        <Can permission="app.deploy">
          <Button onClick={() => setOpenCreate(true)}>
            <PlusIcon className="size-4" />
            {t("start")}
          </Button>
        </Can>
      }
    >
      {/* Sub-navigation: Active | Previews | Pending | History (#797),
          plus the Metrics | Logs | Traces signal gateways (#892).
          Sits directly under the page header, above the table. Each
          fleet tab carries a live count badge sourced from the server's
          own totalCount; Pending's badge is the approval-queue size.
          Signal tabs carry no badge. */}
      <div
        role="tablist"
        aria-label={t("tabs.ariaLabel")}
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {DEPLOYMENT_TABS.map((tabKey) => {
          const active = tab === tabKey;
          const sig = signalTab(tabKey);
          const count = sig ? null : (tabCounts?.[tabKey as FleetTab]?.totalCount ?? null);
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
              <span>{sig ? SIGNAL_COPY[sig].label : t(`tabs.${tabKey}`)}</span>
              {count !== null && count > 0 && (
                <span
                  className={
                    "inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-xs tabular-nums " +
                    (active ? "bg-muted text-foreground" : "bg-muted/70 text-muted-foreground")
                  }
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {tab === "active" && (
        // Clarify that Active is the live + in-flight set, not a
        // deploy-progress queue, and point operators at History for the
        // rows the supersede transition retires. Hardcoded copy mirrors
        // the signal-tab strings above (#892).
        <p className="text-muted-foreground text-xs">
          Active shows the current live and in-progress rollouts, one live deployment per app and
          environment. Superseded, failed, and rolled-back rollouts move to History.
        </p>
      )}

      {signal ? (
        // Gateway placeholder ported from /observe/deployments (#892).
        <EmptyState
          icon={SIGNAL_COPY[signal].icon}
          title={`Deployment ${SIGNAL_COPY[signal].label}`}
          description={SIGNAL_COPY[signal].description}
          actionHref={SIGNAL_COPY[signal].actionHref}
          actionLabel={`Open ${SIGNAL_COPY[signal].label} explorer`}
        />
      ) : (
        <TooltipProvider delayDuration={300}>
          <DataTable
            label="Deployments"
            controller={table}
            columns={columns}
            getRowId={(d) => d.id}
            rowHref={(d) => `/deployments/${d.id}`}
            rowClassName={() => INTERACTIVE_CELLS}
            searchPlaceholder={t("filterApp")}
            // Selection drives the bulk bar, which has nothing to offer
            // an operator who holds none of the deploy permissions.
            selection={hasAnyAction ? selection : undefined}
            bulkActions={() => (
              <>
                {mixedSelection ? (
                  <span className="text-destructive text-xs">{t("bulk.mixedWarning")}</span>
                ) : (
                  <span className="text-muted-foreground text-xs">
                    {t("bulk.summary", { envs: summaryEnvs })}
                  </span>
                )}
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={!allInFlight || bulkRunning || !canDeploy}
                  onClick={() => setPendingBulk({ kind: "abort", deployments: selectedDeploys })}
                >
                  {bulkRunning ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <StopCircleIcon className="size-4" />
                  )}
                  {t("bulk.cancelButton", { count: selectedDeploys.length })}
                </Button>
                <Button
                  size="sm"
                  disabled={!allTerminal || bulkRunning || !canDeploy}
                  onClick={() => setPendingBulk({ kind: "redeploy", deployments: selectedDeploys })}
                >
                  {bulkRunning ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <RotateCcwIcon className="size-4" />
                  )}
                  {t("bulk.redeployButton", { count: selectedDeploys.length })}
                </Button>
              </>
            )}
            empty={{
              icon: <BoxIcon className="size-5" />,
              title: t("emptyTitle"),
              description: t("emptyDescription"),
              actionHref: "/apps",
              actionLabel: t("openApps"),
            }}
            emptyFiltered={{
              title: "No matching deployments",
              description:
                "No deployment in this tab matches that search. It looks at the app, environment, branch, image tag, and commit — try another term, or clear the search to see the whole tab.",
            }}
          />
        </TooltipProvider>
      )}

      {renderStartDialog({ open: openCreate, onOpenChange: setOpenCreate })}

      <ConfirmDialog
        open={pendingAction !== null && pendingAction.kind !== "abort"}
        onOpenChange={(next) => {
          if (!next) setPendingAction(null);
        }}
        title={
          pendingAction && pendingAction.kind !== "abort"
            ? ACTION_COPY[pendingAction.kind].title(pendingAction.deployment)
            : ""
        }
        description={
          pendingAction && pendingAction.kind !== "abort"
            ? ACTION_COPY[pendingAction.kind].description(pendingAction.deployment)
            : ""
        }
        confirmLabel={
          pendingAction && pendingAction.kind !== "abort"
            ? ACTION_COPY[pendingAction.kind].confirmLabel
            : "Confirm"
        }
        destructive={
          pendingAction && pendingAction.kind !== "abort"
            ? ACTION_COPY[pendingAction.kind].destructive
            : false
        }
        onConfirm={async () => {
          if (pendingAction && pendingAction.kind !== "abort") {
            await runAction(pendingAction.kind, pendingAction.deployment);
          }
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
          pendingAction?.kind === "abort" ? ACTION_COPY.abort.title(pendingAction.deployment) : ""
        }
        description={
          pendingAction?.kind === "abort"
            ? ACTION_COPY.abort.description(pendingAction.deployment)
            : ""
        }
        confirmLabel={t("confirm.abortConfirm")}
        destructive
        onConfirm={async (reason) => {
          if (pendingAction?.kind === "abort") {
            await runAction("abort", pendingAction.deployment, reason);
          }
        }}
      />

      {/* Bulk-cancel confirm dialog: requires a reason — same backend
          boundary as the per-row abort. */}
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
        description={
          pendingBulk?.kind === "abort"
            ? t("bulk.confirmCancel.description", { envs: summaryEnvs })
            : ""
        }
        confirmLabel={t("bulk.confirmCancel.confirm")}
        destructive
        onConfirm={async (reason) => {
          if (pendingBulk?.kind === "abort") {
            await runBulk("abort", pendingBulk.deployments, reason);
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
        description={
          pendingBulk?.kind === "redeploy"
            ? t("bulk.confirmRedeploy.description", { envs: summaryEnvs })
            : ""
        }
        confirmLabel={t("bulk.confirmRedeploy.confirm")}
        onConfirm={async () => {
          if (pendingBulk?.kind === "redeploy") {
            await runBulk("redeploy", pendingBulk.deployments);
          }
        }}
      />
    </PageShell>
  );
}

// The action cluster is broken out so the inline tooltip / icon-button
// row doesn't blow up the parent's render. ``onAction`` hoists state up
// to the parent so the confirm dialogs stay singletons. (The per-app
// DeploymentRowActionsView gates and renders differently, so it is not
// reused here.)
interface FleetDeploymentRowActionsProps {
  deployment: AstroliftDeployment;
  canDeploy: boolean;
  canApprove: boolean;
  canRollback: boolean;
  busy: boolean;
  onAction: (kind: ActionKind) => void;
}

function FleetDeploymentRowActions({
  deployment: d,
  canDeploy,
  canApprove,
  canRollback,
  busy,
  onAction,
}: FleetDeploymentRowActionsProps) {
  const t = useTranslations("lists.deployments");

  const inFlight = IN_FLIGHT.has(d.status);
  const canApproveThis = d.status === "pending_approval" && canApprove && !d.triggeredByMe;
  const canAbortThis = inFlight && canDeploy;
  const canRollbackThis = (d.status === "running" || d.status === "failed") && canRollback;
  const canRedeployThis = !inFlight && d.status !== "running" && canDeploy;
  const hasCommitLink = Boolean(d.repoUrl && d.commitSha);

  return (
    <div className="inline-flex items-center justify-end gap-1">
      {canApproveThis && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              disabled={busy}
              onClick={() => onAction("approve")}
              aria-label={t("actions.approve")}
            >
              <CheckIcon className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("actions.approve")}</TooltipContent>
        </Tooltip>
      )}
      {canAbortThis && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="text-destructive hover:text-destructive size-8"
              disabled={busy}
              onClick={() => onAction("abort")}
              aria-label={t("actions.abort")}
            >
              <StopCircleIcon className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("actions.abort")}</TooltipContent>
        </Tooltip>
      )}
      {canRollbackThis && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              disabled={busy}
              onClick={() => onAction("rollback")}
              aria-label={t("actions.rollback")}
            >
              <UndoIcon className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("actions.rollback")}</TooltipContent>
        </Tooltip>
      )}
      {canRedeployThis && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              disabled={busy}
              onClick={() => onAction("redeploy")}
              aria-label={t("actions.redeploy")}
            >
              <RotateCcwIcon className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("actions.redeploy")}</TooltipContent>
        </Tooltip>
      )}
      {/* The whole row links here too; the explicit button keeps the
          affordance discoverable and keyboard-reachable from the action
          cluster. */}
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" asChild>
            <Link href={`/deployments/${d.id}`} aria-label={t("actions.viewLogs")}>
              <ExternalLinkIcon className="size-4" />
            </Link>
          </Button>
        </TooltipTrigger>
        <TooltipContent>{t("actions.viewLogs")}</TooltipContent>
      </Tooltip>
      {/* Overflow kebab: rare actions stay one click deep so the row's
          inline-action row doesn't grow as we add things. */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            disabled={busy}
            aria-label={t("actions.more")}
          >
            <MoreHorizontalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {hasCommitLink && (
            <DropdownMenuItem
              onClick={() => {
                window.open(
                  `${d.repoUrl.replace(/\/$/, "")}/commit/${d.commitSha}`,
                  "_blank",
                  "noopener,noreferrer"
                );
              }}
            >
              <ExternalLinkIcon className="size-4" />
              {t("actions.viewCommit")}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            onClick={() => {
              navigator.clipboard
                .writeText(d.id)
                .then(() => toast.success(t("actions.copyOk")))
                .catch(() => toast.error(t("actions.copyFail")));
            }}
          >
            <BoxIcon className="size-4" />
            {t("actions.copyGuid")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
