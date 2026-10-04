"use client";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useLocalListState, type ListDefinition } from "@/components/list/use-list-state";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  LIST_MODEL_SUBSCRIPTION_TARGETS,
  LIST_CLUSTER_MODEL_SUBSCRIPTIONS,
} from "@/graphql/models/shared-models.queries";
import {
  SUBSCRIBE_CLUSTER_MODEL,
  REVOKE_MODEL_SUBSCRIPTION,
} from "@/graphql/models/shared-models.mutations";
import type {
  ClusterModelFieldsFragment,
  ListModelSubscriptionTargetsQuery,
  ListModelSubscriptionTargetsQueryVariables,
  ListClusterModelSubscriptionsQuery,
  ListClusterModelSubscriptionsQueryVariables,
  SubscribeClusterModelMutation,
  SubscribeClusterModelMutationVariables,
  RevokeModelSubscriptionMutation,
  RevokeModelSubscriptionMutationVariables,
} from "@/graphql/__generated__/operations";
import type {
  ModelSubscriptionsPanelProps,
  SubscriptionStatus,
  SubscriptionRequest,
  RevokeSubscriptionRequest,
} from "./ModelSubscriptionsPanel";
import { subscriptionModelResult } from "./shared-model-write-results";

const statuses: readonly string[] = ["pending", "active", "revoking", "revoked", "failed"];
export function useModelSubscriptions(
  model: ClusterModelFieldsFragment,
  blocked: boolean,
  onRefreshDeployment: () => void,
  targetsEnabled = true
): ModelSubscriptionsPanelProps {
  const t = useTranslations("models.shared.subscriptions");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const skipped = orgLoading || !!orgError || !org?.id || org.id !== model.organizationId;
  const definitions = useMemo(() => {
    const list = (id: string, search: string): ListDefinition => ({
      id,
      fields: [],
      searchPlaceholder: search,
      defaultSort: [],
      views: [{ key: "all", label: t("title"), filters: {} }],
      paging: "numbered",
      pageSizes: [10, 25, 50],
      defaultPageSize: 10,
    });
    return {
      targets: list("models.shared.subscriptionTargets", t("targetSearch")),
      subscriptions: list("models.shared.subscriptions", t("subscriptionSearch")),
    };
  }, [t]);
  const targetList = useLocalListState(definitions.targets),
    subscriptionList = useLocalListState(definitions.subscriptions);
  const targets = useQuery<
    ListModelSubscriptionTargetsQuery,
    ListModelSubscriptionTargetsQueryVariables
  >(LIST_MODEL_SUBSCRIPTION_TARGETS, {
    variables: {
      organizationId: model.organizationId,
      modelDeploymentId: model.id,
      search: targetList.state.q || null,
      page: targetList.state.page,
      pageSize: targetList.state.pageSize,
    },
    skip: skipped || !targetsEnabled,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const subscriptions = useQuery<
    ListClusterModelSubscriptionsQuery,
    ListClusterModelSubscriptionsQueryVariables
  >(LIST_CLUSTER_MODEL_SUBSCRIPTIONS, {
    variables: {
      organizationId: model.organizationId,
      modelDeploymentId: model.id,
      search: subscriptionList.state.q || null,
      appEnvironmentId: null,
      page: subscriptionList.state.page,
      pageSize: subscriptionList.state.pageSize,
    },
    skip: skipped,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const targetPage =
    skipped || !targetsEnabled
      ? null
      : (targets.data ?? (targets.loading || targets.error ? targets.previousData : undefined))
          ?.clusterModelSubscriptionTargetsPage;
  const subscriptionPage = skipped
    ? null
    : (
        subscriptions.data ??
        (subscriptions.loading || subscriptions.error ? subscriptions.previousData : undefined)
      )?.clusterModelSubscriptionsPage;
  const invalidSubscriptions = !!subscriptionPage?.items.some(
    (row) => row.modelDeploymentId !== model.id || !statuses.includes(row.status)
  );
  const invalidTargets = !!targetPage?.items.some(
    (row) =>
      !row.environmentId ||
      !Number.isSafeInteger(row.environmentVersion) ||
      row.environmentVersion < 1
  );
  const targetError =
      orgError?.message ??
      (skipped || invalidTargets ? t("changed") : (targets.error?.message ?? null)),
    subscriptionError =
      orgError?.message ??
      (skipped || invalidSubscriptions ? t("changed") : (subscriptions.error?.message ?? null));
  const scopeKey = JSON.stringify([
    org?.id,
    model.id,
    model.version,
    model.clusterId,
    model.providerId,
    skipped,
    blocked,
    targetsEnabled,
  ]);
  const [scope, setScope] = useState({ key: scopeKey, revision: 0 });
  if (scope.key !== scopeKey) setScope({ key: scopeKey, revision: scope.revision + 1 });
  const latest = useRef({ key: scopeKey, revision: scope.revision }),
    mounted = useRef(true),
    lifecycle = useRef(0),
    busy = useRef(false);
  useLayoutEffect(() => {
    latest.current = { key: scopeKey, revision: scope.revision };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current += 1;
    };
  }, []);
  const [subscribe] = useMutation<
    SubscribeClusterModelMutation,
    SubscribeClusterModelMutationVariables
  >(SUBSCRIBE_CLUSTER_MODEL, { fetchPolicy: "no-cache" });
  const [revoke] = useMutation<
    RevokeModelSubscriptionMutation,
    RevokeModelSubscriptionMutationVariables
  >(REVOKE_MODEL_SUBSCRIPTION, { fetchPolicy: "no-cache" });
  async function write(request: SubscriptionRequest | RevokeSubscriptionRequest) {
    const epoch = lifecycle.current,
      revision = scope.revision;
    const current = () =>
      mounted.current &&
      lifecycle.current === epoch &&
      latest.current.key === scopeKey &&
      latest.current.revision === revision;
    if (
      !current() ||
      busy.current ||
      blocked ||
      skipped ||
      request.organizationId !== model.organizationId ||
      request.modelId !== model.id ||
      request.modelVersion !== model.version ||
      request.expectedClusterId !== model.clusterId ||
      request.expectedProviderId !== model.providerId
    )
      return { accepted: false as const, message: t("changed") };
    if ("environmentId" in request) {
      if (!targetsEnabled) return { accepted: false as const, message: t("changed") };
      if (
        targetError ||
        targets.loading ||
        !model.subscriptionsEnabled ||
        model.runtimeSupported !== true ||
        !/^[a-z][a-z0-9_]{0,31}$/.test(request.alias) ||
        !targetPage?.items.some(
          (row) =>
            row.environmentId === request.environmentId &&
            row.environmentVersion === request.environmentVersion &&
            row.clusterId === model.clusterId &&
            row.eligible
        )
      )
        return { accepted: false as const, message: t("changed") };
    } else if (
      subscriptionError ||
      subscriptions.loading ||
      model.runtimeSupported !== true ||
      !subscriptionPage?.items.some(
        (row) =>
          row.id === request.subscriptionId &&
          row.version === request.subscriptionVersion &&
          row.canRevoke
      )
    )
      return { accepted: false as const, message: t("changed") };
    busy.current = true;
    try {
      const envelope =
        "environmentId" in request
          ? (
              await subscribe({
                variables: {
                  input: {
                    organizationId: request.organizationId,
                    modelDeploymentId: request.modelId,
                    expectedClusterId: request.expectedClusterId,
                    expectedProviderId: request.expectedProviderId,
                    appEnvironmentId: request.environmentId,
                    alias: request.alias,
                    ifMatchVersion: request.modelVersion,
                    ifMatchEnvironmentVersion: request.environmentVersion,
                  },
                },
              })
            ).data?.subscribeClusterModel
          : (
              await revoke({
                variables: {
                  input: {
                    organizationId: request.organizationId,
                    id: request.subscriptionId,
                    expectedClusterId: request.expectedClusterId,
                    expectedProviderId: request.expectedProviderId,
                    ifMatchVersion: request.subscriptionVersion,
                    ifMatchDeploymentVersion: request.modelVersion,
                  },
                },
              })
            ).data?.revokeModelSubscription;
      if (!current()) return { accepted: false as const, message: t("changed") };
      return subscriptionModelResult(envelope, request, t("requestFailed"));
    } catch (error) {
      return {
        accepted: false as const,
        message: current() && error instanceof Error ? error.message : t("changed"),
      };
    } finally {
      busy.current = false;
    }
  }
  return {
    deployment: {
      id: model.id,
      organizationId: model.organizationId,
      version: model.version,
      name: model.name,
      clusterId: model.clusterId,
      providerId: model.providerId,
      subscriptionsEnabled: model.subscriptionsEnabled,
      runtimeAdmission:
        model.runtimeSupported === true
          ? "configured"
          : model.runtimeSupported === false
            ? "unsupported"
            : "unknown",
    },
    targets: {
      list: targetList,
      rows: invalidTargets
        ? []
        : (targetPage?.items ?? []).map((row) => ({
            id: row.environmentId,
            version: row.environmentVersion,
            clusterId: row.clusterId,
            appSlug: row.appSlug,
            environmentName: row.environmentName,
            admission: row.eligible ? "allowed" : "denied",
            reason: row.reason ?? null,
          })),
      loading: orgLoading || (!skipped && targets.loading && !targetPage),
      stale: blocked || (!!targetPage && (targets.loading || !!targetError)),
      error: targetError ? { message: targetError } : null,
      totalCount: targetPage?.totalCount ?? null,
      nextCursor: null,
      onRetry: () => {
        if (!skipped) void targets.refetch().catch(() => {});
      },
    },
    subscriptions: {
      list: subscriptionList,
      rows: invalidSubscriptions
        ? []
        : (subscriptionPage?.items ?? []).map((row) => ({
            id: row.id,
            version: row.version,
            alias: row.alias,
            bindingPrefix: row.bindingPrefix,
            appSlug: row.appSlug,
            environmentName: row.environmentName,
            status: row.status as SubscriptionStatus,
            desiredRevision: row.desiredRevision,
            appliedRevision: row.appliedRevision,
            reason: row.reason ?? null,
            canRevoke: row.canRevoke,
          })),
      loading: orgLoading || (!skipped && subscriptions.loading && !subscriptionPage),
      stale: blocked || (!!subscriptionPage && (subscriptions.loading || !!subscriptionError)),
      error: subscriptionError ? { message: subscriptionError } : null,
      totalCount: subscriptionPage?.totalCount ?? null,
      nextCursor: null,
      onRetry: () => {
        if (!skipped) void subscriptions.refetch().catch(() => {});
      },
    },
    onSubscribe: write,
    onRevoke: write,
    onAccepted: () => {
      if (!skipped) {
        void Promise.allSettled([targets.refetch(), subscriptions.refetch()]);
        onRefreshDeployment();
      }
    },
  };
}
