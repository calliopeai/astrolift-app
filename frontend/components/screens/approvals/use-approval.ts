"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { APPROVE_DEPLOYMENT, REJECT_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import {
  GET_DEPLOYMENT,
  GET_DEPLOYMENT_APPROVAL_HISTORY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftDeployment,
  AstroliftDeploymentApprovalHistoryEntry,
} from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

interface DeploymentResp {
  astroliftDeployment: AstroliftDeployment | null;
}

interface ApprovalHistoryResp {
  astroliftDeploymentApprovalHistory: AstroliftDeploymentApprovalHistoryEntry[];
}

/**
 * The deployment behind the approval page plus its approve / reject
 * mutations. The data half of ApprovalScreen. `onApprove` / `onReject`
 * follow the ConfirmDialog contract: they resolve on success and throw
 * (keeping the dialog open) on failure.
 */
export function useApproval(id: string) {
  const t = useTranslations("lists.approval");
  const { can } = useMyPermissions();
  const { data, loading, refetch } = useQuery<DeploymentResp>(GET_DEPLOYMENT, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });
  const deployment = data?.astroliftDeployment ?? null;

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, {
    refetchQueries: [
      { query: GET_DEPLOYMENT, variables: { id } },
      { query: GET_DEPLOYMENT_APPROVAL_HISTORY, variables: { deploymentId: id } },
    ],
  });

  const [reject, rejectState] = useMutation<{
    rejectDeployment: MutationResultLite<AstroliftDeployment>;
  }>(REJECT_DEPLOYMENT, {
    refetchQueries: [
      { query: GET_DEPLOYMENT, variables: { id } },
      { query: GET_DEPLOYMENT_APPROVAL_HISTORY, variables: { deploymentId: id } },
    ],
  });

  async function onApprove(): Promise<void> {
    const { data: result } = await approve({
      variables: { input: { id: deployment!.id } },
    });
    const r = result?.approveDeployment;
    if (r?.ok) {
      toast.success(t("toasts.approved", { status: r.data?.status ?? "ok" }));
      refetch().catch(() => {});
    } else {
      throw new Error(r?.errors[0]?.message ?? t("toasts.approveFailed"));
    }
  }

  async function onReject(reason: string): Promise<void> {
    const { data: result } = await reject({
      variables: { input: { id: deployment!.id, reason } },
    });
    const r = result?.rejectDeployment;
    if (r?.ok) {
      toast.success(t("toasts.rejected"));
      refetch().catch(() => {});
    } else {
      throw new Error(r?.errors[0]?.message ?? t("toasts.rejectFailed"));
    }
  }

  return {
    deployment,
    loading,
    canApprovePermission: can("app.approve_deploy"),
    busy: approveState.loading || rejectState.loading,
    onApprove,
    onReject,
  };
}

/** The approval history of one deployment. The data half of ApprovalHistoryPanel. */
export function useApprovalHistory(deploymentId: string) {
  const { data, loading } = useQuery<ApprovalHistoryResp>(GET_DEPLOYMENT_APPROVAL_HISTORY, {
    variables: { deploymentId },
    fetchPolicy: "cache-and-network",
  });
  return { entries: data?.astroliftDeploymentApprovalHistory ?? [], loading };
}
