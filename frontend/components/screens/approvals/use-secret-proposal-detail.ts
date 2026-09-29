"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  APPROVE_SECRET_CHANGE,
  REJECT_SECRET_CHANGE,
  WITHDRAW_SECRET_CHANGE,
} from "@/graphql/services/services.mutations";
import { GET_SECRET_CHANGE_PROPOSAL } from "@/graphql/services/services.queries";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface GetResp {
  astroliftSecretChangeProposal: AstroliftSecretChangeProposal | null;
}

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

interface ApproveResp {
  approveSecretChange: MutationResultLite<AstroliftSecretChangeProposal>;
}
interface RejectResp {
  rejectSecretChange: MutationResultLite<AstroliftSecretChangeProposal>;
}
interface WithdrawResp {
  withdrawSecretChange: MutationResultLite<AstroliftSecretChangeProposal>;
}

/**
 * One secret-change proposal (#488) and the approve / reject / withdraw
 * mutations. The data half of SecretProposalDetailScreen. Each handler
 * throws on failure so the confirm dialog shows the error inline.
 */
export function useSecretProposalDetail(proposalId: string) {
  const t = useTranslations("approvals.secretProposalDetail");
  const router = useRouter();
  const { can } = useMyPermissions();
  const canApprove = can("secret.approve");

  const { data, loading, refetch } = useQuery<GetResp>(GET_SECRET_CHANGE_PROPOSAL, {
    variables: { id: proposalId },
    fetchPolicy: "cache-and-network",
  });

  const refetchQueries = [{ query: GET_SECRET_CHANGE_PROPOSAL, variables: { id: proposalId } }];
  const [approve, approveState] = useMutation<ApproveResp>(APPROVE_SECRET_CHANGE, {
    refetchQueries,
  });
  const [reject, rejectState] = useMutation<RejectResp>(REJECT_SECRET_CHANGE, {
    refetchQueries,
  });
  const [withdraw, withdrawState] = useMutation<WithdrawResp>(WITHDRAW_SECRET_CHANGE, {
    refetchQueries,
  });

  async function onApprove() {
    const { data } = await approve({
      variables: { input: { proposalId } },
    });
    const result = data?.approveSecretChange;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.approveFailed"));
    }
    toast.success(t("toasts.approveOk"));
    refetch().catch(() => {});
  }

  async function onReject(reason: string) {
    const { data } = await reject({
      variables: { input: { proposalId, reason } },
    });
    const result = data?.rejectSecretChange;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.rejectFailed"));
    }
    toast.success(t("toasts.rejectOk"));
    refetch().catch(() => {});
  }

  async function onWithdraw() {
    const { data } = await withdraw({ variables: { input: { proposalId } } });
    const result = data?.withdrawSecretChange;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.withdrawFailed"));
    }
    toast.success(t("toasts.withdrawOk"));
    router.push("/approvals");
  }

  return {
    proposal: data?.astroliftSecretChangeProposal ?? null,
    loading: loading && !data,
    canApprove,
    approving: approveState.loading,
    rejecting: rejectState.loading,
    withdrawing: withdrawState.loading,
    onApprove,
    onReject,
    onWithdraw,
  };
}
