"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  BULK_APPROVE_DEPLOYMENTS,
  BULK_REJECT_DEPLOYMENTS,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftBulkDeploymentResultData,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface ListResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

interface BulkApproveResp {
  bulkApproveDeployments: MutationResultLite<AstroliftBulkDeploymentResultData>;
}

interface BulkRejectResp {
  bulkRejectDeployments: MutationResultLite<AstroliftBulkDeploymentResultData>;
}

/**
 * Every pending_approval deploy across the org (polled every 30s) plus the
 * bulk approve / reject mutations. The data half of ApprovalsQueueScreen.
 * `onBulkApprove` / `onBulkReject` follow the ConfirmDialog contract: they
 * resolve when at least one deploy went through and throw (keeping the
 * dialog open) otherwise; the view clears its selection on resolve.
 */
export function useApprovalsQueue() {
  const t = useTranslations("lists.approvalsQueue");
  const { can } = useMyPermissions();

  const { data, loading, refetch } = useQuery<ListResp>(LIST_DEPLOYMENTS, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  const pending = (data?.astroliftDeployments ?? []).filter((d) => d.status === "pending_approval");

  const [bulkApprove, bulkApproveState] = useMutation<BulkApproveResp>(BULK_APPROVE_DEPLOYMENTS, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }],
  });
  const [bulkReject, bulkRejectState] = useMutation<BulkRejectResp>(BULK_REJECT_DEPLOYMENTS, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }],
  });

  function reportBulkResult(
    label: string,
    data: AstroliftBulkDeploymentResultData | null | undefined
  ) {
    if (!data) {
      throw new Error(t("toasts.unexpected"));
    }
    if (data.failedCount === 0) {
      toast.success(t("toasts.allOk", { label, count: data.succeededCount }));
    } else if (data.succeededCount === 0) {
      throw new Error(
        t("toasts.allFailed", {
          label,
          count: data.failedCount,
          message: data.results[0]?.errors[0]?.message ?? "",
        })
      );
    } else {
      toast.warning(
        t("toasts.partial", {
          label,
          succeeded: data.succeededCount,
          failed: data.failedCount,
        })
      );
    }
  }

  async function onBulkApprove(ids: string[]): Promise<void> {
    const { data } = await bulkApprove({ variables: { input: { deploymentIds: ids } } });
    const result = data?.bulkApproveDeployments;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.approveFailed"));
    }
    reportBulkResult(t("approveLabel"), result.data);
    refetch().catch(() => {});
  }

  async function onBulkReject(ids: string[], reason: string): Promise<void> {
    const { data } = await bulkReject({
      variables: { input: { deploymentIds: ids, reason } },
    });
    const result = data?.bulkRejectDeployments;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.rejectFailed"));
    }
    reportBulkResult(t("rejectLabel"), result.data);
    refetch().catch(() => {});
  }

  return {
    pending,
    /** True only for the first load; later polls keep the list on screen. */
    loading: loading && !data,
    canApprove: can("app.approve_deploy"),
    approving: bulkApproveState.loading,
    rejecting: bulkRejectState.loading,
    onBulkApprove,
    onBulkReject,
  };
}
