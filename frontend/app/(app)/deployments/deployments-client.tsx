"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import {
  BoxIcon,
  CheckIcon,
  ClockIcon,
  ExternalLinkIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  PlusIcon,
  RotateCcwIcon,
  StopCircleIcon,
  UndoIcon,
} from "lucide-react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ConfirmDialogWithReason } from "@/components/ConfirmDialogWithReason";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { ViewToggle } from "@/components/ViewToggle";
import { useViewToggle } from "@/hooks/use-view-toggle";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { StartDeploymentDialog } from "./start-deployment-dialog";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface Resp {
  astroliftDeployments: AstroliftDeployment[];
}

const statusToDot: Record<DeploymentStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  pending_approval: "warn",
  pending: "warn",
  deploying: "pending",
  redeploying: "pending",
  running: "ok",
  failed: "error",
  superseded: "muted",
  rolled_back: "muted",
};

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

// Status taxonomy used by the pill row + bulk-action gating. "Active"
// is a virtual alias for the in-flight set so triage-focused operators
// can clear the "what's currently moving" bucket in one click.
const IN_FLIGHT: ReadonlySet<DeploymentStatus> = new Set([
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
]);

const TERMINAL: ReadonlySet<DeploymentStatus> = new Set(["failed", "rolled_back", "superseded"]);

// Sub-navigation tabs (#797). Previews and the approval queue are
// subdivisions of the deployment fleet, not separate primitives, so
// they live as tabs here rather than as top-level nav entries. The tab
// replaces the older status-filter pills — the pills were themselves a
// status axis, so stacking both would be redundant.
type DeploymentTab = "active" | "previews" | "pending" | "history";

const DEPLOYMENT_TABS: readonly DeploymentTab[] = ["active", "previews", "pending", "history"];

interface TabCount {
  tab: DeploymentTab;
  count: number;
  labelKey: string;
}

// A preview deployment is bound to a pull request. ``prNumber`` is a
// non-null Int that defaults to 0 when there's no PR, so the test is
// ``> 0`` — never ``!= null`` (which would match every row).
function isPreview(d: AstroliftDeployment): boolean {
  return d.prNumber > 0;
}

function tabMatches(tab: DeploymentTab, d: AstroliftDeployment): boolean {
  switch (tab) {
    case "active":
      // Everything currently moving or live, excluding the approval
      // queue (its own tab) and previews (their own tab).
      return (
        !isPreview(d) &&
        d.status !== "pending_approval" &&
        (IN_FLIGHT.has(d.status) || d.status === "running")
      );
    case "previews":
      return isPreview(d);
    case "pending":
      return d.status === "pending_approval";
    case "history":
      return TERMINAL.has(d.status);
  }
}

type ActionKind = "approve" | "abort" | "rollback" | "redeploy";

interface PendingAction {
  kind: ActionKind;
  deployment: AstroliftDeployment;
}

interface BulkAction {
  kind: "abort" | "redeploy";
  deployments: AstroliftDeployment[];
}

export function DeploymentsClient() {
  const t = useTranslations("lists.deployments");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [openCreate, setOpenCreate] = React.useState(false);
  const [viewMode, setViewMode] = useViewToggle("astrolift_view_deployments", "list");

  // Filters synced to URL so refresh / back-button preserves the view.
  const rawTab = searchParams.get("tab") as DeploymentTab | null;
  const tab: DeploymentTab =
    rawTab && DEPLOYMENT_TABS.includes(rawTab) ? rawTab : "active";
  const [appFilter, setAppFilter] = React.useState<string>(
    () => searchParams.get("app") ?? ""
  );

  function updateFilter(key: string, value: string, defaultValue = "") {
    const params = new URLSearchParams(searchParams.toString());
    if (value && value !== defaultValue) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  // ``active`` is the default tab, so omit it from the URL to keep the
  // canonical /deployments link clean.
  const setTab = (v: DeploymentTab) => updateFilter("tab", v, "active");

  React.useEffect(() => {
    const id = setTimeout(() => updateFilter("app", appFilter), 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appFilter]);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const { can } = useMyPermissions();
  const {
    data,
    loading,
    refetch: refetchList,
  } = useQuery<Resp>(LIST_DEPLOYMENTS, {
    variables: { limit: 100 },
    // Live push covers freshness; keep a slow safety-net poll in case
    // the WS drops and we miss reconnect.
    pollInterval: 30000,
  });
  const allDeployments = React.useMemo(
    () => data?.astroliftDeployments ?? [],
    [data?.astroliftDeployments]
  );

  const list = React.useMemo(() => {
    return allDeployments.filter((d) => {
      if (!tabMatches(tab, d)) return false;
      if (appFilter && !d.registeredAppSlug.toLowerCase().includes(appFilter.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [allDeployments, tab, appFilter]);

  // Live push: any status transition for any deployment in the org
  // triggers a list refetch. The backend dedupes per-row, and refetch
  // is cheap because the page is bounded to 100 rows.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    onData: () => {
      refetchList().catch(() => {
        // swallowed: a failed refetch is recovered by the next push or
        // by the safety-net poll above.
      });
    },
  });

  // Pre-compute action allowance once to avoid re-checks in render.
  const canDeploy = can("app.deploy");
  const canApprove = can("app.approve_deploy");
  const canRollback = can("app.rollback");
  const hasAnyAction = canDeploy || canApprove || canRollback;

  // Drop selections for rows that disappeared from the queue between
  // polls (got resolved by someone else) or got filtered out. Computed
  // at render time over the *filtered* list — same pattern as the
  // approvals queue.
  const selectedDeploys = React.useMemo(
    () => list.filter((d) => selected.has(d.id)),
    [list, selected]
  );

  // Tab badge counts pull from the unfiltered ``allDeployments`` so each
  // tab shows its true total regardless of the active app filter. The
  // Pending badge doubles as the live approval-queue size — kept fresh
  // by the lifecycle subscription refetch below.
  const counts = React.useMemo<TabCount[]>(() => {
    return DEPLOYMENT_TABS.map((tabKey) => ({
      tab: tabKey,
      count: allDeployments.reduce((acc, d) => (tabMatches(tabKey, d) ? acc + 1 : acc), 0),
      labelKey: `tabs.${tabKey}`,
    }));
  }, [allDeployments]);

  const refetch = [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }];
  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { refetchQueries: refetch });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { refetchQueries: refetch });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { refetchQueries: refetch });
  const [redeploy, redeployState] = useMutation<{
    redeployApp: MutationResultLite<AstroliftDeployment>;
  }>(REDEPLOY_APP, { refetchQueries: refetch });

  const busy =
    approveState.loading || abortState.loading || rollbackState.loading || redeployState.loading;

  const [bulkRunning, setBulkRunning] = React.useState(false);

  function reportResult(
    label: string,
    result: MutationResultLite<AstroliftDeployment> | null | undefined
  ) {
    if (!result) return;
    if (result.ok) {
      toast.success(`${label}: ${result.data?.status ?? "ok"}`);
    } else {
      throw new Error(result.errors[0]?.message ?? `${label} failed`);
    }
  }

  const [pendingAction, setPendingAction] = React.useState<PendingAction | null>(null);
  const [pendingBulk, setPendingBulk] = React.useState<BulkAction | null>(null);

  async function runAction(kind: ActionKind, d: AstroliftDeployment, reason?: string) {
    if (kind === "approve") {
      const { data } = await approve({ variables: { input: { id: d.id } } });
      reportResult("approveDeployment", data?.approveDeployment);
    } else if (kind === "abort") {
      // abort/reject now require a non-empty reason at the backend
      // boundary (#419). Routed through ConfirmDialogWithReason below.
      const { data } = await abort({
        variables: { input: { id: d.id, reason: reason ?? "" } },
      });
      reportResult("abortDeployment", data?.abortDeployment);
    } else if (kind === "rollback") {
      const { data } = await rollback({ variables: { input: { id: d.id } } });
      reportResult("rollbackDeployment", data?.rollbackDeployment);
    } else if (kind === "redeploy") {
      const { data } = await redeploy({ variables: { input: { id: d.id } } });
      reportResult("redeployApp", data?.redeployApp);
    }
  }

  // Bulk-cancel + bulk-redeploy fan out across the existing single-id
  // mutations. The issue body explicitly notes "Pure FE; no backend
  // changes (mutations all exist)" so we don't reach for a bespoke
  // bulk endpoint — Promise.allSettled converges the partial-success
  // surface into a single toast.
  async function runBulk(
    kind: "abort" | "redeploy",
    deployments: AstroliftDeployment[],
    reason?: string
  ) {
    setBulkRunning(true);
    try {
      const results = await Promise.allSettled(
        deployments.map((d) =>
          kind === "abort"
            ? abort({ variables: { input: { id: d.id, reason: reason ?? "" } } }).then(
                ({ data }) => {
                  const r = data?.abortDeployment;
                  if (!r?.ok) {
                    throw new Error(r?.errors[0]?.message ?? "abort failed");
                  }
                  return r;
                }
              )
            : redeploy({ variables: { input: { id: d.id } } }).then(({ data }) => {
                const r = data?.redeployApp;
                if (!r?.ok) {
                  throw new Error(r?.errors[0]?.message ?? "redeploy failed");
                }
                return r;
              })
        )
      );
      const failed = results.filter((r) => r.status === "rejected").length;
      const succeeded = results.length - failed;
      const labelKey = kind === "abort" ? "bulk.toasts.abortLabel" : "bulk.toasts.redeployLabel";
      if (failed === 0) {
        toast.success(t("bulk.toasts.allOk", { label: t(labelKey), count: succeeded }));
      } else if (succeeded === 0) {
        const first = results.find((r) => r.status === "rejected") as
          | PromiseRejectedResult
          | undefined;
        const message = first?.reason instanceof Error ? first.reason.message : "";
        throw new Error(t("bulk.toasts.allFailed", { label: t(labelKey), count: failed, message }));
      } else {
        toast.warning(t("bulk.toasts.partial", { label: t(labelKey), succeeded, failed }));
      }
      setSelected(new Set());
      refetchList().catch(() => {});
    } finally {
      setBulkRunning(false);
    }
  }

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

  function toggleRow(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function toggleAllVisible() {
    setSelected((prev) => {
      // If everything in the filtered view is already selected, clear
      // the selection. Otherwise select the full filtered set.
      const allSelected = list.length > 0 && list.every((d) => prev.has(d.id));
      if (allSelected) return new Set();
      const next = new Set(prev);
      for (const d of list) next.add(d.id);
      return next;
    });
  }

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

  function navigateToDeployment(id: string) {
    router.push(`/deployments/${id}`);
  }

  function onRowKeyDown(event: React.KeyboardEvent<HTMLTableRowElement>, id: string) {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      navigateToDeployment(id);
    }
  }

  const allVisibleSelected = list.length > 0 && list.every((d) => selected.has(d.id));
  const someVisibleSelected = !allVisibleSelected && list.some((d) => selected.has(d.id));

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      actions={
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onChange={setViewMode} />
          <Can permission="app.deploy">
            <Button onClick={() => setOpenCreate(true)}>
              <PlusIcon className="size-4" />
              {t("start")}
            </Button>
          </Can>
        </div>
      }
    >
      {/* Sub-navigation: Active | Previews | Pending | History (#797).
          Sits directly under the page header, above the filter row.
          Each tab carries a live count badge; Pending's badge is the
          approval-queue size. */}
      <div
        role="tablist"
        aria-label={t("tabs.ariaLabel")}
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {counts.map(({ tab: tabKey, count, labelKey }) => {
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
              <span>{t(labelKey)}</span>
              {count > 0 && (
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

      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder={t("filterApp")}
          value={appFilter}
          onChange={(e) => setAppFilter(e.target.value)}
          className="max-w-xs"
        />
        <span className="text-muted-foreground ml-auto text-xs">
          {t("counts", { filtered: list.length, total: allDeployments.length })}
        </span>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
                actionHref="/apps"
                actionLabel={t("openApps")}
              />
            </div>
          ) : (
            <TooltipProvider delayDuration={300}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      {hasAnyAction && (
                        <input
                          type="checkbox"
                          aria-label={t("bulk.selectAllLabel")}
                          checked={allVisibleSelected}
                          ref={(el) => {
                            if (el) el.indeterminate = someVisibleSelected;
                          }}
                          onChange={toggleAllVisible}
                          className="size-4"
                          onClick={(e) => e.stopPropagation()}
                        />
                      )}
                    </TableHead>
                    <TableHead className="w-6"></TableHead>
                    <TableHead>{t("columns.appEnv")}</TableHead>
                    <TableHead>{t("columns.image")}</TableHead>
                    <TableHead>{t("columns.trigger")}</TableHead>
                    <TableHead>{t("columns.status")}</TableHead>
                    <TableHead>{t("columns.duration")}</TableHead>
                    <TableHead>{t("columns.started")}</TableHead>
                    <TableHead className="w-44 text-right">{t("columns.actions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.map((d) => (
                    <DeploymentRow
                      key={d.id}
                      deployment={d}
                      checked={selected.has(d.id)}
                      hasAnyAction={hasAnyAction}
                      canDeploy={canDeploy}
                      canApprove={canApprove}
                      canRollback={canRollback}
                      busy={busy || bulkRunning}
                      onToggle={() => toggleRow(d.id)}
                      onNavigate={() => navigateToDeployment(d.id)}
                      onKeyDown={(e) => onRowKeyDown(e, d.id)}
                      onAction={(kind) => setPendingAction({ kind, deployment: d })}
                    />
                  ))}
                </TableBody>
              </Table>
            </TooltipProvider>
          )}
        </CardContent>
      </Card>

      {/* Scope 2 — sticky bulk action bar. Appears only when at least
          one row is checked. Mixed-state selections get an inline
          warning instead of a fired-but-half-skipped batch. */}
      {selectedDeploys.length > 0 && (
        <div className="bg-background pointer-events-auto fixed inset-x-0 bottom-0 z-30 border-t shadow-lg">
          <div className="mx-auto flex max-w-6xl flex-col items-stretch gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              <p className="font-medium">{t("bulk.selected", { count: selectedDeploys.length })}</p>
              {mixedSelection ? (
                <p className="text-destructive mt-0.5 text-xs">{t("bulk.mixedWarning")}</p>
              ) : (
                <p className="text-muted-foreground mt-0.5 text-xs">
                  {t("bulk.summary", { envs: summaryEnvs })}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Button
                variant="ghost"
                onClick={() => setSelected(new Set())}
                disabled={bulkRunning}
                className="min-h-11 w-full sm:w-auto"
              >
                {t("bulk.clear")}
              </Button>
              <Button
                variant="destructive"
                disabled={!allInFlight || bulkRunning || !canDeploy}
                onClick={() => setPendingBulk({ kind: "abort", deployments: selectedDeploys })}
                className="min-h-11 w-full sm:w-auto"
              >
                {bulkRunning ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <StopCircleIcon className="size-4" />
                )}
                {t("bulk.cancelButton", { count: selectedDeploys.length })}
              </Button>
              <Button
                disabled={!allTerminal || bulkRunning || !canDeploy}
                onClick={() => setPendingBulk({ kind: "redeploy", deployments: selectedDeploys })}
                className="min-h-11 w-full sm:w-auto"
              >
                {bulkRunning ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <RotateCcwIcon className="size-4" />
                )}
                {t("bulk.redeployButton", { count: selectedDeploys.length })}
              </Button>
            </div>
          </div>
        </div>
      )}

      <StartDeploymentDialog open={openCreate} onOpenChange={setOpenCreate} />

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

      <ConfirmDialogWithReason
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
        reasonLabel={t("confirm.abortReasonLabel")}
        reasonPlaceholder={t("confirm.abortReasonPlaceholder")}
        reasonRequiredError={t("confirm.reasonRequired")}
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
      <ConfirmDialogWithReason
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
        reasonLabel={t("bulk.confirmCancel.reasonLabel")}
        reasonPlaceholder={t("bulk.confirmCancel.reasonPlaceholder")}
        reasonRequiredError={t("confirm.reasonRequired")}
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

// Row component is broken out so the inline tooltip / icon-button row
// doesn't blow up the parent's render. ``onAction`` hoists state up
// to the parent so the confirm dialogs stay singletons.
interface DeploymentRowProps {
  deployment: AstroliftDeployment;
  checked: boolean;
  hasAnyAction: boolean;
  canDeploy: boolean;
  canApprove: boolean;
  canRollback: boolean;
  busy: boolean;
  onToggle: () => void;
  onNavigate: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLTableRowElement>) => void;
  onAction: (kind: ActionKind) => void;
}

function DeploymentRow({
  deployment: d,
  checked,
  hasAnyAction,
  canDeploy,
  canApprove,
  canRollback,
  busy,
  onToggle,
  onNavigate,
  onKeyDown,
  onAction,
}: DeploymentRowProps) {
  const t = useTranslations("lists.deployments");

  const inFlight = IN_FLIGHT.has(d.status);
  const canApproveThis = d.status === "pending_approval" && canApprove && !d.triggeredByMe;
  const canAbortThis = inFlight && canDeploy;
  const canRollbackThis = (d.status === "running" || d.status === "failed") && canRollback;
  const canRedeployThis = !inFlight && d.status !== "running" && canDeploy;
  const hasCommitLink = Boolean(d.repoUrl && d.commitSha);

  // Scope 4 — row-click navigation. Avoids global ``onClick`` on
  // interactive descendants by guarding ``event.target ===
  // event.currentTarget`` is fragile; instead, child interactive
  // elements call ``stopPropagation`` themselves below.
  return (
    <TableRow
      tabIndex={0}
      role="link"
      aria-label={t("rowAriaLabel", {
        app: d.registeredAppSlug,
        env: d.environmentName,
      })}
      onClick={onNavigate}
      onKeyDown={onKeyDown}
      className="hover:bg-accent/30 focus-visible:outline-ring cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-[-2px]"
    >
      <TableCell className="w-10" onClick={(e) => e.stopPropagation()}>
        {hasAnyAction && (
          <input
            type="checkbox"
            aria-label={t("bulk.selectRowLabel", {
              app: d.registeredAppSlug,
              env: d.environmentName,
            })}
            checked={checked}
            onChange={onToggle}
            className="size-4"
            onClick={(e) => e.stopPropagation()}
          />
        )}
      </TableCell>
      <TableCell className="w-6">
        <StatusDot status={statusToDot[d.status]} />
      </TableCell>
      <TableCell>
        <div className="font-medium">{d.registeredAppSlug}</div>
        <div className="text-muted-foreground text-xs">
          env <span className="font-mono">{d.environmentName}</span>
          {d.workloadSlug && (
            <>
              {" "}
              · workload <span className="font-mono">{d.workloadSlug}</span>
            </>
          )}
        </div>
      </TableCell>
      <TableCell className="font-mono text-xs">{d.imageTag || "—"}</TableCell>
      <TableCell>
        <Badge variant="outline">{d.triggerKind}</Badge>
      </TableCell>
      <TableCell>
        <DeploymentStatusPill status={d.status} label={t(`statusOptions.${d.status}`)} />
        {d.approvalsRequired > 0 && (
          <div className="text-muted-foreground mt-1 text-xs">
            {t("approvalsCount", {
              received: d.approvalsReceived,
              required: d.approvalsRequired,
            })}
          </div>
        )}
      </TableCell>
      <TableCell className="font-mono text-xs">
        <span className="inline-flex items-center gap-1">
          <ClockIcon className="size-3" />
          {formatDuration(d.durationSeconds)}
        </span>
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        {d.startedAt
          ? new Date(d.startedAt).toLocaleString()
          : new Date(d.createdAt).toLocaleString()}
      </TableCell>
      {/* Scope 3 — per-row inline action buttons. The kebab survives
          as overflow for less-common actions (view commit / copy guid)
          so the row doesn't grow indefinitely. */}
      <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
        <div className="inline-flex items-center justify-end gap-1">
          {canApproveThis && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction("approve");
                  }}
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
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction("abort");
                  }}
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
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction("rollback");
                  }}
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
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction("redeploy");
                  }}
                  aria-label={t("actions.redeploy")}
                >
                  <RotateCcwIcon className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{t("actions.redeploy")}</TooltipContent>
            </Tooltip>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                onClick={(e) => {
                  e.stopPropagation();
                  onNavigate();
                }}
                aria-label={t("actions.viewLogs")}
              >
                <ExternalLinkIcon className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("actions.viewLogs")}</TooltipContent>
          </Tooltip>
          {/* Overflow kebab: rare actions stay one click deep so the
              row's inline-action row doesn't grow as we add things. */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                disabled={busy}
                onClick={(e) => e.stopPropagation()}
                aria-label={t("actions.more")}
              >
                <MoreHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
              {hasCommitLink && (
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
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
                onClick={(e) => {
                  e.stopPropagation();
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
      </TableCell>
    </TableRow>
  );
}
