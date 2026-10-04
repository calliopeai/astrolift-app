"use client";
import { useRef, useState } from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { GET_MODEL_CONNECTION_REQUEST } from "@/graphql/models/model-connections.queries";
import {
  APPROVE_MODEL_CONNECTION_REQUEST,
  CANCEL_MODEL_CONNECTION_REQUEST,
  FINALIZE_MODEL_CONNECTION_REQUEST,
  REJECT_MODEL_CONNECTION_REQUEST,
} from "@/graphql/models/model-connections.mutations";
import type {
  GetModelConnectionRequestQuery,
  GetModelConnectionRequestQueryVariables,
  ModelConnectionRequestFieldsFragment,
  ApproveModelConnectionRequestMutation,
  ApproveModelConnectionRequestMutationVariables,
  RejectModelConnectionRequestMutation,
  RejectModelConnectionRequestMutationVariables,
  CancelModelConnectionRequestMutation,
  CancelModelConnectionRequestMutationVariables,
  FinalizeModelConnectionRequestMutation,
  FinalizeModelConnectionRequestMutationVariables,
} from "@/graphql/__generated__/operations";
import type {
  ConnectionDecisionReview,
  ConnectionDecisionResult,
  ModelConnectionRequestProps,
} from "./ModelConnectionRequestScreen";
import { useConnectionEpoch, useModelConnectionSupport } from "./use-model-connection-context";
export function useModelConnectionRequest(
  id: string,
  initialVersion: number,
  reviewer: boolean
): ModelConnectionRequestProps {
  const t = useTranslations("models.shared.connections"),
    { org, loading: orgLoading, error: orgError } = useActiveOrg(),
    { user, loading: userLoading, error: userError } = useMe(),
    support = useModelConnectionSupport(),
    client = useApolloClient();
  const [version, setVersion] = useState(initialVersion);
  const admitted =
    !!org?.id &&
    !!user?.id &&
    !orgLoading &&
    !userLoading &&
    !orgError &&
    !userError &&
    !!id &&
    Number.isSafeInteger(version) &&
    version > 0;
  const scopeKey = JSON.stringify([user?.id, org?.id, id, reviewer]);
  const query = useQuery<GetModelConnectionRequestQuery, GetModelConnectionRequestQueryVariables>(
    GET_MODEL_CONNECTION_REQUEST,
    {
      variables: { input: { id, ifMatchVersion: version }, review: reviewer },
      skip: !admitted || support.supported !== true,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const data = query.data ?? (query.loading || query.error ? query.previousData : undefined),
    source = reviewer ? data?.inbox : data?.own;
  const invalid =
    !!source &&
    (source.id !== id ||
      source.organizationId !== org?.id ||
      // Detail reads return the current row, not a CAS result for the URL hint.
      !Number.isSafeInteger(source.version) ||
      source.version < version ||
      [source.canApprove, source.canReject, source.canCancel, source.canFinalize].some(
        (value) => typeof value !== "boolean"
      ));
  const row = admitted && support.supported === true && !invalid ? (source ?? null) : null;
  const error =
    orgError?.message ??
    userError?.message ??
    (invalid || (!admitted && !orgLoading && !userLoading)
      ? t("changed")
      : (query.error?.message ?? null));
  const epoch = useConnectionEpoch([
    scopeKey,
    admitted,
    support.supported,
    support.error,
    row,
    query.loading,
    error,
    version,
  ]);
  const busy = useRef(false);
  async function submit(candidate: ConnectionDecisionReview): Promise<ConnectionDecisionResult> {
    const current = epoch(),
      expected = candidate.request;
    const allowed =
      candidate.action === "approve"
        ? row?.canApprove
        : candidate.action === "reject"
          ? row?.canReject
          : candidate.action === "cancel"
            ? row?.canCancel
            : row?.canFinalize;
    if (
      !current() ||
      busy.current ||
      !admitted ||
      support.supported !== true ||
      support.error ||
      query.loading ||
      error ||
      !row ||
      !allowed ||
      JSON.stringify(row) !== JSON.stringify(expected)
    )
      return { accepted: false, message: t("changed") };
    busy.current = true;
    const variables = { input: { id: expected.id, ifMatchVersion: expected.version } };
    try {
      const reply =
        candidate.action === "approve"
          ? (
              await client.mutate<
                ApproveModelConnectionRequestMutation,
                ApproveModelConnectionRequestMutationVariables
              >({ mutation: APPROVE_MODEL_CONNECTION_REQUEST, variables, fetchPolicy: "no-cache" })
            ).data?.approveModelConnectionRequest
          : candidate.action === "reject"
            ? (
                await client.mutate<
                  RejectModelConnectionRequestMutation,
                  RejectModelConnectionRequestMutationVariables
                >({ mutation: REJECT_MODEL_CONNECTION_REQUEST, variables, fetchPolicy: "no-cache" })
              ).data?.rejectModelConnectionRequest
            : candidate.action === "cancel"
              ? (
                  await client.mutate<
                    CancelModelConnectionRequestMutation,
                    CancelModelConnectionRequestMutationVariables
                  >({
                    mutation: CANCEL_MODEL_CONNECTION_REQUEST,
                    variables,
                    fetchPolicy: "no-cache",
                  })
                ).data?.cancelModelConnectionRequest
              : (
                  await client.mutate<
                    FinalizeModelConnectionRequestMutation,
                    FinalizeModelConnectionRequestMutationVariables
                  >({
                    mutation: FINALIZE_MODEL_CONNECTION_REQUEST,
                    variables,
                    fetchPolicy: "no-cache",
                  })
                ).data?.finalizeModelConnectionRequest;
      if (!current()) return { accepted: false, message: t("changed") };
      if (!reply?.ok)
        return { accepted: false, message: reply?.errors[0]?.message ?? t("uncertain") };
      const value = reply.data;
      const correlated =
        value &&
        value.id === expected.id &&
        value.organizationId === expected.organizationId &&
        value.modelDeploymentId === expected.modelDeploymentId &&
        value.appId === expected.appId &&
        value.appEnvironmentId === expected.appEnvironmentId &&
        value.clusterId === expected.clusterId &&
        value.providerId === expected.providerId &&
        value.alias === expected.alias &&
        Number.isSafeInteger(value.version) &&
        value.version >= expected.version;
      return { accepted: true, row: correlated ? value : null };
    } catch {
      return { accepted: false, message: current() ? t("uncertain") : t("changed") };
    } finally {
      busy.current = false;
    }
  }
  return {
    scopeKey,
    row,
    loading: orgLoading || userLoading || (query.loading && !row),
    stale: !admitted || query.loading || !!error,
    error,
    supported: support.supported,
    supportError: support.error,
    onRetrySupport: support.onRetry,
    onRetry: () => {
      if (admitted && support.supported === true) void query.refetch().catch(() => {});
    },
    onSubmit: submit,
    onAccepted: async (value: ModelConnectionRequestFieldsFragment | null) => {
      if (!value) return;
      try {
        await query.refetch({ input: { id, ifMatchVersion: value.version }, review: reviewer });
      } finally {
        setVersion(value.version);
      }
    },
  };
}
