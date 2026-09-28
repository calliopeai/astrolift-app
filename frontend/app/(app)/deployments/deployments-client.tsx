"use client";

import { gql } from "@apollo/client";
import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
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
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, useCursorTable, useRowSelection } from "@/components/data-table";
import type { Column } from "@/components/data-table";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { StartDeploymentDialog } from "./start-deployment-dialog";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface DeploymentsPageResp {
  astroliftDeploymentsPage: {
    items: AstroliftDeployment[];
    nextCursor: string | null;
    totalCount: number | null;
  };
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

// Status taxonomy used by the bulk-action gating: cancel applies only to
// a uniformly in-flight selection, redeploy only to a uniformly terminal
// one. (The tab split is a server-side filter now — see TAB_FILTERS.)
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
type FleetTab = "active" | "previews" | "pending" | "history";

// Observe signal surfaces folded into the fleet tabs (#892). They are
// gateway placeholders into the fleet-wide explorers — they render an
// EmptyState instead of deployment rows, so they carry no badge count.
type SignalTab = "metrics" | "logs" | "traces";

type DeploymentTab = FleetTab | SignalTab;

const DEPLOYMENT_TABS: readonly DeploymentTab[] = [
  "active",
  "previews",
  "pending",
  "history",
  "metrics",
  "logs",
  "traces",
];

/**
 * What each fleet tab asks the server for (#1235).
 *
 * This replaces the old client-side `tabMatches` predicate, which ran
 * over a `limit: 100` fetch: the 101st deployment did not exist as far
 * as this page was concerned, and every badge count was wrong past 100.
 * `astroliftDeploymentsPage` takes the status *group* and the preview
 * split directly, so the tabs are now four different queries rather
 * than four filters over one capped page.
 *
 * Membership is unchanged from `tabMatches`:
 *   active   in-flight (minus the approval queue) + the live row, no previews
 *   previews anything raised from a pull request, any status
 *   pending  the approval queue
 *   history  the terminal states
 */
type TabFilter = { statuses?: readonly DeploymentStatus[]; isPreview?: boolean };

const TAB_FILTERS: Record<FleetTab, TabFilter> = {
  active: {
    statuses: ["pending", "deploying", "redeploying", "running"],
    isPreview: false,
  },
  previews: { isPreview: true },
  pending: { statuses: ["pending_approval"] },
  history: { statuses: ["failed", "rolled_back", "superseded"] },
};

/**
 * Tab badge counts, one `totalCount` per fleet tab (#1235).
 *
 * Aliased into a single round trip and asked for `limit: 1`, because
 * the badge wants the size of the result set and none of its rows. The
 * counts deliberately ignore the search box — a badge is the size of
 * the tab, not of the current filter, which is what the old
 * `allDeployments.reduce(...)` intended before the 100-row cap made it
 * a lie.
 */
const DEPLOYMENT_TAB_COUNTS = gql`
  query DeploymentTabCounts(
    $activeStatuses: [String!]
    $activeIsPreview: Boolean
    $previewsIsPreview: Boolean
    $pendingStatuses: [String!]
    $historyStatuses: [String!]
  ) {
    active: astroliftDeploymentsPage(
      statuses: $activeStatuses
      isPreview: $activeIsPreview
      limit: 1
    ) {
      totalCount
    }
    previews: astroliftDeploymentsPage(isPreview: $previewsIsPreview, limit: 1) {
      totalCount
    }
    pending: astroliftDeploymentsPage(statuses: $pendingStatuses, limit: 1) {
      totalCount
    }
    history: astroliftDeploymentsPage(statuses: $historyStatuses, limit: 1) {
      totalCount
    }
  }
`;

type TabCountsResp = Record<FleetTab, { totalCount: number | null } | null>;

const TAB_COUNT_VARIABLES = {
  activeStatuses: TAB_FILTERS.active.statuses,
  activeIsPreview: TAB_FILTERS.active.isPreview,
  previewsIsPreview: TAB_FILTERS.previews.isPreview,
  pendingStatuses: TAB_FILTERS.pending.statuses,
  historyStatuses: TAB_FILTERS.history.statuses,
};

// Mutations refetch by operation name rather than by document +
// variables: the list's variables now carry the tab filter, the page
// cursor and the search term, so no literal variables object names the
// query the operator is actually looking at.
const REFETCH_LIST = ["ListDeploymentsPage", "DeploymentTabCounts"];

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

function signalTab(tab: DeploymentTab): SignalTab | null {
  return tab === "metrics" || tab === "logs" || tab === "traces" ? tab : null;
}

// DataTable stretches the row's link across the whole row (an ::after on
// the first cell), and that overlay paints above the un-positioned cells
// beside it. The selection checkbox and the action buttons have to be
// lifted back on top of it or the only thing a click in those cells can
// do is navigate.
const INTERACTIVE_CELLS =
  "[&>td:has([role=checkbox])]:relative [&>td:has([role=checkbox])]:z-10 " +
  "[&>td:last-child]:relative [&>td:last-child]:z-10";

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

  // Tab synced to URL so refresh / back-button preserves the view. The
  // search term and page size are the controller's business (?dep-q=,
  // ?dep-size=); the cursor deliberately stays out of the URL.
  const rawTab = searchParams.get("tab") as DeploymentTab | null;
  const tab: DeploymentTab = rawTab && DEPLOYMENT_TABS.includes(rawTab) ? rawTab : "active";
  const signal = signalTab(tab);

  // ``active`` is the default tab, so omit it from the URL to keep the
  // canonical /deployments link clean.
  function setTab(value: DeploymentTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (value !== "active") {
      params.set("tab", value);
    } else {
      params.delete("tab");
    }
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  const { can } = useMyPermissions();
  const selection = useRowSelection();
  const { clear: clearSelection } = selection;

  const variables = React.useMemo<TabFilter>(
    () => (signal ? {} : TAB_FILTERS[tab as FleetTab]),
    [tab, signal]
  );

  const table = useCursorTable<AstroliftDeployment>({
    query: LIST_DEPLOYMENTS_PAGE,
    variables,
    extract: (d) => (d as DeploymentsPageResp | undefined)?.astroliftDeploymentsPage,
    searchVariable: "search",
    urlKey: "dep",
    // Live push covers freshness; keep a slow safety-net poll in case
    // the WS drops and we miss reconnect.
    pollInterval: 30000,
    // Signal tabs render a gateway placeholder, not deployment rows.
    skip: Boolean(signal),
  });

  const { data: countsData, refetch: refetchCounts } = useQuery<TabCountsResp>(
    DEPLOYMENT_TAB_COUNTS,
    {
      variables: TAB_COUNT_VARIABLES,
      fetchPolicy: "cache-and-network",
      pollInterval: 30000,
    }
  );

  const { refetch: refetchList } = table;

  // Live push: any status transition for any deployment in the org
  // refetches the visible page and the badge counts. The backend
  // dedupes per-row, and both queries are one page wide.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    onData: () => {
      refetchList();
      refetchCounts().catch(() => {
        // swallowed: a failed refetch is recovered by the next push or
        // by the safety-net poll above.
      });
    },
  });

  // A selection is scoped to the tab it was made in — the bulk actions
  // are status-uniform by construction, so carrying ids across a tab
  // switch could only produce a batch the operator cannot see.
  React.useEffect(() => {
    clearSelection();
  }, [tab, clearSelection]);

  // Pre-compute action allowance once to avoid re-checks in render.
  const canDeploy = can("app.deploy");
  const canApprove = can("app.approve_deploy");
  const canRollback = can("app.rollback");
  const hasAnyAction = canDeploy || canApprove || canRollback;

  // useRowSelection keeps ids across pages, so the bulk bar has to be
  // able to resolve a row that is no longer on screen: both the
  // in-flight/terminal gating and the environment summary read the
  // deployment, not just its id. Every page walked past folds into this
  // index, and a re-fetched row overwrites its older copy.
  const [rowIndex, setRowIndex] = React.useState<ReadonlyMap<string, AstroliftDeployment>>(
    () => new Map()
  );
  React.useEffect(() => {
    if (table.rows.length === 0) return;
    setRowIndex((prev) => {
      const next = new Map(prev);
      for (const d of table.rows) next.set(d.id, d);
      return next;
    });
  }, [table.rows]);

  const selectedIds = selection.selectedIds;
  const selectedDeploys = React.useMemo(
    () =>
      selectedIds.map((id) => rowIndex.get(id)).filter((d): d is AstroliftDeployment => Boolean(d)),
    [selectedIds, rowIndex]
  );

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { refetchQueries: REFETCH_LIST });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { refetchQueries: REFETCH_LIST });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { refetchQueries: REFETCH_LIST });
  const [redeploy, redeployState] = useMutation<{
    redeployApp: MutationResultLite<AstroliftDeployment>;
  }>(REDEPLOY_APP, { refetchQueries: REFETCH_LIST });

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
      // boundary (#419). Routed through ConfirmDialog (with a reason) below.
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
      clearSelection();
      refetchList();
      refetchCounts().catch(() => {});
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
        <DeploymentRowActions
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
          const count = sig ? null : (countsData?.[tabKey as FleetTab]?.totalCount ?? null);
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
// to the parent so the confirm dialogs stay singletons.
interface DeploymentRowActionsProps {
  deployment: AstroliftDeployment;
  canDeploy: boolean;
  canApprove: boolean;
  canRollback: boolean;
  busy: boolean;
  onAction: (kind: ActionKind) => void;
}

function DeploymentRowActions({
  deployment: d,
  canDeploy,
  canApprove,
  canRollback,
  busy,
  onAction,
}: DeploymentRowActionsProps) {
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
