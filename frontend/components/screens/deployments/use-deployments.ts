"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { ActionKind } from "./deployments-format";
import { DEPLOYMENTS_LIST, deploymentsVariables } from "./deployments-list";

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

// Mutations refetch by operation name: the list's variables carry the
// view's filters, the cursor and the search, so no literal variables
// object names the page the operator is looking at.
const REFETCH_LIST = ["ListDeploymentsPage"];

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
 * Apps › Deployments: URL list state, one cursor page of
 * `astroliftDeploymentsPage`, live push held behind the "new" pill, and the
 * lifecycle mutations. The data half of DeploymentsScreen. `runAction` and
 * `runBulk` throw on failure so ConfirmDialog holds open with the reason.
 */
export function useDeployments() {
  const t = useTranslations("lists.deployments");
  const list = useListState(DEPLOYMENTS_LIST);
  const { state } = list;
  // Fixed per visit: Today's midnight and the since windows do not move
  // under the reader.
  const [now] = React.useState(() => Date.now());
  const variables = deploymentsVariables(list.filters, state, now);

  const query = useQuery<DeploymentsPageResp>(LIST_DEPLOYMENTS_PAGE, {
    variables,
    fetchPolicy: "cache-and-network",
    // Live push covers freshness; the slow poll is the safety net for a
    // dropped socket.
    pollInterval: 30000,
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftDeploymentsPage;
  const items = React.useMemo(() => page?.items ?? [], [page]);
  const held = useHeldRows(items, (d) => d.id, {
    // New deployments arrive on the first page only.
    live: state.after === null,
    resetKey: JSON.stringify(list.filters) + state.q + state.pageSize,
  });

  const { refetch } = query;
  // Any status transition in the org refetches the page in hand; the held
  // rows keep it still while new ones wait behind the pill.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    onData: () => {
      refetch().catch(() => {
        // Recovered by the next push or the safety-net poll.
      });
    },
  });

  // The bulk bar resolves selected ids to deployments, including rows on
  // a page walked past; every row seen folds in, newest copy wins.
  const [byId, setById] = React.useState<ReadonlyMap<string, AstroliftDeployment>>(() => new Map());
  React.useEffect(() => {
    if (items.length === 0) return;
    setById((prev) => {
      const next = new Map(prev);
      for (const d of items) next.set(d.id, d);
      return next;
    });
  }, [items]);

  const { can } = useMyPermissions();
  const canDeploy = can("app.deploy");
  const canApprove = can("app.approve_deploy");
  const canRollback = can("app.rollback");

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: REFETCH_LIST });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: REFETCH_LIST });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: REFETCH_LIST });
  const [redeploy, redeployState] = useMutation<{
    redeployApp: MutationResultLite<AstroliftDeployment>;
  }>(REDEPLOY_APP, { onQueryUpdated: refetchAfterMutation, refetchQueries: REFETCH_LIST });

  const busy =
    approveState.loading || abortState.loading || rollbackState.loading || redeployState.loading;
  const [bulkRunning, setBulkRunning] = React.useState(false);

  async function runAction(kind: ActionKind, d: AstroliftDeployment, reason?: string) {
    if (kind === "approve") {
      const { data } = await approve({ variables: { input: { id: d.id } } });
      reportResult("approveDeployment", data?.approveDeployment);
    } else if (kind === "abort") {
      // abort requires a non-empty reason at the backend boundary (#419).
      const { data } = await abort({ variables: { input: { id: d.id, reason: reason ?? "" } } });
      reportResult("abortDeployment", data?.abortDeployment);
    } else if (kind === "rollback") {
      const { data } = await rollback({ variables: { input: { id: d.id } } });
      reportResult("rollbackDeployment", data?.rollbackDeployment);
    } else if (kind === "redeploy") {
      const { data } = await redeploy({ variables: { input: { id: d.id } } });
      reportResult("redeployApp", data?.redeployApp);
    }
  }

  // Bulk cancel and redeploy fan out over the single-id mutations (no bulk
  // endpoint); allSettled folds partial success into one toast.
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
                  if (!r?.ok) throw new Error(r?.errors[0]?.message ?? "abort failed");
                  return r;
                }
              )
            : redeploy({ variables: { input: { id: d.id } } }).then(({ data }) => {
                const r = data?.redeployApp;
                if (!r?.ok) throw new Error(r?.errors[0]?.message ?? "redeploy failed");
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
    } finally {
      await refetchAfterMutation({ refetch });
      setBulkRunning(false);
    }
  }

  return {
    list,
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: query.loading && !data,
    // Rows on screen answer the previous question while the next loads.
    stale: query.loading && !query.data && Boolean(data),
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: () => {
      void refetch();
    },
    nextCursor: page?.nextCursor ?? null,
    totalCount: page?.totalCount ?? null,
    deploymentsById: byId,
    canDeploy,
    canApprove,
    canRollback,
    busy,
    bulkRunning,
    runAction,
    runBulk,
    startHref: "/deployments/new",
  };
}
