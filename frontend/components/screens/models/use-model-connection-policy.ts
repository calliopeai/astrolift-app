"use client";
import { useRef } from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { GET_MODEL_HOSTING_ACTION } from "@/graphql/models/hosting.queries";
import {
  GET_MODEL_CONNECTION_RESTRICTION,
  GET_ORGANIZATION_MODEL_CONNECTION_POLICY,
} from "@/graphql/models/model-connections.queries";
import {
  SET_MODEL_CONNECTION_RESTRICTION,
  UPDATE_ORGANIZATION_MODEL_CONNECTION_POLICY,
} from "@/graphql/models/model-connections.mutations";
import type {
  ClusterModelFieldsFragment,
  GetModelHostingActionQuery,
  GetModelHostingActionQueryVariables,
  GetModelConnectionRestrictionQuery,
  GetModelConnectionRestrictionQueryVariables,
  GetOrganizationModelConnectionPolicyQuery,
  GetOrganizationModelConnectionPolicyQueryVariables,
  SetModelConnectionRestrictionMutation,
  SetModelConnectionRestrictionMutationVariables,
  UpdateOrganizationModelConnectionPolicyMutation,
  UpdateOrganizationModelConnectionPolicyMutationVariables,
} from "@/graphql/__generated__/operations";
import type {
  ConnectionPolicyReview,
  ConnectionPolicyResult,
  ModelConnectionPolicyProps,
} from "./ModelConnectionPolicyPanel";
import { useConnectionEpoch, useModelConnectionSupport } from "./use-model-connection-context";
export function useModelConnectionPolicy(
  model: ClusterModelFieldsFragment | null,
  blocked = false
): ModelConnectionPolicyProps {
  const t = useTranslations("models.shared.connections"),
    { org, loading: orgLoading, error: orgError } = useActiveOrg(),
    { user, loading: userLoading, error: userError } = useMe(),
    support = useModelConnectionSupport(),
    client = useApolloClient();
  const admitted =
    !blocked &&
    !!org?.id &&
    !!user?.id &&
    !orgLoading &&
    !userLoading &&
    !orgError &&
    !userError &&
    (!model || model.organizationId === org.id);
  const hosting = useQuery<GetModelHostingActionQuery, GetModelHostingActionQueryVariables>(
    GET_MODEL_HOSTING_ACTION,
    {
      variables: { organizationId: org?.id ?? "" },
      skip: !model || !admitted || support.supported !== true,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const canEdit =
    admitted &&
    (!model ||
      (!hosting.loading && !hosting.error && hosting.data?.modelHostingAction.allowed === true));
  const organization = useQuery<
    GetOrganizationModelConnectionPolicyQuery,
    GetOrganizationModelConnectionPolicyQueryVariables
  >(GET_ORGANIZATION_MODEL_CONNECTION_POLICY, {
    variables: { organizationId: org?.id ?? "" },
    skip: !!model || !admitted || support.supported !== true,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const restriction = useQuery<
    GetModelConnectionRestrictionQuery,
    GetModelConnectionRestrictionQueryVariables
  >(GET_MODEL_CONNECTION_RESTRICTION, {
    variables: {
      input: {
        organizationId: model?.organizationId ?? "",
        modelDeploymentId: model?.id ?? "",
        expectedClusterId: model?.clusterId ?? "",
        expectedProviderId: model?.providerId ?? "",
      },
    },
    skip: !model || !canEdit || support.supported !== true,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const loading =
    orgLoading ||
    userLoading ||
    (model ? hosting.loading || restriction.loading : organization.loading);
  const queryError = model ? (hosting.error ?? restriction.error) : organization.error;
  const error =
    orgError?.message ??
    userError?.message ??
    queryError?.message ??
    (model && admitted && !hosting.loading && hosting.data?.modelHostingAction.allowed === false
      ? hosting.data.modelHostingAction.reason
      : null) ??
    null;
  const policy =
    admitted && support.supported === true
      ? ((model
          ? (restriction.data ?? (loading || error ? restriction.previousData : undefined))
              ?.modelConnectionRestriction
          : (organization.data ?? (loading || error ? organization.previousData : undefined))
              ?.organizationModelConnectionPolicy) ?? null)
      : null;
  const invalid =
    !!policy &&
    (!Number.isSafeInteger(policy.version) ||
      policy.version < 0 ||
      !Number.isSafeInteger(policy.requiredApprovals) ||
      policy.requiredApprovals < 1 ||
      policy.requiredApprovals > 16 ||
      !["AUTO", "REQUIRE_APPROVAL", "DENY"].includes(policy.mode));
  const scopeKey = JSON.stringify([
      user?.id,
      org?.id,
      model?.id,
      model?.clusterId,
      model?.providerId,
    ]),
    epoch = useConnectionEpoch([
      scopeKey,
      model?.version,
      canEdit,
      support.supported,
      support.error,
      policy,
      loading,
      error,
      invalid,
    ]),
    busy = useRef(false);
  async function submit(candidate: ConnectionPolicyReview): Promise<ConnectionPolicyResult> {
    const current = epoch();
    if (
      !current() ||
      busy.current ||
      !canEdit ||
      support.supported !== true ||
      support.error ||
      loading ||
      error ||
      invalid ||
      !policy ||
      JSON.stringify(candidate.policy) !== JSON.stringify(policy) ||
      !["AUTO", "REQUIRE_APPROVAL", "DENY"].includes(candidate.draft.mode) ||
      !Number.isSafeInteger(candidate.draft.requiredApprovals) ||
      candidate.draft.requiredApprovals < 1 ||
      candidate.draft.requiredApprovals > 16
    )
      return { accepted: false, message: t("changed") };
    busy.current = true;
    try {
      const base = { organizationId: org!.id, ifMatchVersion: policy.version, ...candidate.draft };
      const reply = model
        ? (
            await client.mutate<
              SetModelConnectionRestrictionMutation,
              SetModelConnectionRestrictionMutationVariables
            >({
              mutation: SET_MODEL_CONNECTION_RESTRICTION,
              variables: {
                input: {
                  ...base,
                  modelDeploymentId: model.id,
                  expectedClusterId: model.clusterId,
                  expectedProviderId: model.providerId,
                  ifMatchDeploymentVersion: model.version,
                },
              },
              fetchPolicy: "no-cache",
            })
          ).data?.setModelConnectionRestriction
        : (
            await client.mutate<
              UpdateOrganizationModelConnectionPolicyMutation,
              UpdateOrganizationModelConnectionPolicyMutationVariables
            >({
              mutation: UPDATE_ORGANIZATION_MODEL_CONNECTION_POLICY,
              variables: { input: base },
              fetchPolicy: "no-cache",
            })
          ).data?.updateOrganizationModelConnectionPolicy;
      if (!current()) return { accepted: false, message: t("changed") };
      if (!reply?.ok)
        return { accepted: false, message: reply?.errors[0]?.message ?? t("uncertain") };
      const row = reply.data,
        correlated =
          row &&
          Number.isSafeInteger(row.version) &&
          row.version >= policy.version &&
          (policy.id == null || row.id === policy.id) &&
          row.mode === candidate.draft.mode &&
          row.requiredApprovals === candidate.draft.requiredApprovals &&
          row.allowSelfApproval === candidate.draft.allowSelfApproval;
      return { accepted: true, policy: correlated ? row : null };
    } catch {
      return { accepted: false, message: current() ? t("uncertain") : t("changed") };
    } finally {
      busy.current = false;
    }
  }
  const retry = () => {
    if (!admitted || support.supported !== true) return;
    if (model) {
      void hosting.refetch().catch(() => {});
      if (canEdit) void restriction.refetch().catch(() => {});
    } else void organization.refetch().catch(() => {});
  };
  return {
    scopeKey,
    restriction: !!model,
    policy: invalid ? null : policy,
    loading,
    stale: !admitted || loading || !!error,
    error: invalid ? t("changed") : error,
    supported: support.supported,
    supportError: support.error,
    canEdit,
    onRetry: retry,
    onRetrySupport: support.onRetry,
    onSubmit: submit,
    onAccepted: async () => {
      if (model) await restriction.refetch();
      else await organization.refetch();
    },
  };
}
