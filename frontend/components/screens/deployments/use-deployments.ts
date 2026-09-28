"use client";

import { gql } from "@apollo/client";
import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable, useRowSelection } from "@/components/data-table";
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

import {
  DEPLOYMENT_TABS,
  signalTab,
  type ActionKind,
  type DeploymentTab,
  type FleetTab,
} from "./deployments-format";

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

export type TabCountsResp = Record<FleetTab, { totalCount: number | null } | null>;

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

/**
 * The fleet walk, tab counts, live push and lifecycle mutations behind
 * /deployments. The data half of DeploymentsScreen. `runAction` and
 * `runBulk` throw on failure so ConfirmDialog holds open with the reason.
 */
export function useDeployments() {
  const t = useTranslations("lists.deployments");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

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

  const { data: tabCounts, refetch: refetchCounts } = useQuery<TabCountsResp>(
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

  async function runAction(kind: ActionKind, d: AstroliftDeployment, reason?: string) {
    if (kind === "approve") {
      const { data } = await approve({ variables: { input: { id: d.id } } });
      reportResult("approveDeployment", data?.approveDeployment);
    } else if (kind === "abort") {
      // abort/reject now require a non-empty reason at the backend
      // boundary (#419). Routed through ConfirmDialog (with a reason).
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

  return {
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
  };
}
