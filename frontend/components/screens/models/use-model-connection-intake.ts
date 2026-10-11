"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useLocalListState, type ListDefinition } from "@/components/list/use-list-state";
import {
  GET_MODEL_CONNECTION_ACTION,
  LIST_MODEL_CONNECTION_TARGETS,
} from "@/graphql/models/model-connections.queries";
import { REQUEST_MODEL_CONNECTION } from "@/graphql/models/model-connections.mutations";
import { subscriptionModelResult } from "./shared-model-write-results";
import { SUBSCRIBE_CLUSTER_MODEL } from "@/graphql/models/shared-models.mutations";
import type {
  ClusterModelFieldsFragment,
  GetModelConnectionActionQuery,
  GetModelConnectionActionQueryVariables,
  ListModelConnectionTargetsQuery,
  ListModelConnectionTargetsQueryVariables,
  RequestModelConnectionMutation,
  RequestModelConnectionMutationVariables,
  SubscribeClusterModelMutation,
  SubscribeClusterModelMutationVariables,
} from "@/graphql/__generated__/operations";
import type {
  ConnectionDestination,
  ConnectionIntakeReview,
  ConnectionWriteResult,
  ModelConnectionIntakeProps,
} from "./ModelConnectionIntakePanel";
import {
  isConnectionGuid,
  useConnectionEpoch,
  useModelConnectionSupport,
} from "./use-model-connection-context";

type Recovery = {
  review: ConnectionIntakeReview;
  request: RequestModelConnectionMutationVariables["input"];
  appSlug: string;
};
export function useModelConnectionIntake(
  model: ClusterModelFieldsFragment,
  blocked: boolean,
  onRefreshDeployment: () => unknown
): ModelConnectionIntakeProps {
  const t = useTranslations("models.shared.connections"),
    { org, loading: orgLoading, error: orgError } = useActiveOrg(),
    { user, loading: userLoading, error: userError } = useMe();
  const support = useModelConnectionSupport(),
    client = useApolloClient();
  const admitted =
    !blocked &&
    !orgLoading &&
    !userLoading &&
    !orgError &&
    !userError &&
    !!user?.id &&
    org?.id === model.organizationId;
  const scopeKey = JSON.stringify([user?.id, org?.id, model.id, model.clusterId, model.providerId]);
  const storageKey = `astrolift.model.connectionRequest:${scopeKey}`;
  const def = useMemo(
    () =>
      ({
        id: "models.connectionTargets",
        fields: [],
        searchPlaceholder: t("target"),
        defaultSort: [],
        views: [{ key: "all", label: t("target"), filters: {} }],
        paging: "numbered",
        pageSizes: [10, 25, 50],
        defaultPageSize: 10,
      }) satisfies ListDefinition,
    [t]
  );
  const list = useLocalListState(def);
  const query = useQuery<ListModelConnectionTargetsQuery, ListModelConnectionTargetsQueryVariables>(
    LIST_MODEL_CONNECTION_TARGETS,
    {
      variables: {
        input: {
          organizationId: model.organizationId,
          modelDeploymentId: model.id,
          expectedClusterId: model.clusterId,
          expectedProviderId: model.providerId,
        },
        search: list.state.q || null,
        page: list.state.page,
        pageSize: list.state.pageSize,
      },
      skip: !admitted || support.supported !== true,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const source = query.error || query.loading ? (query.data ?? query.previousData) : query.data;
  const page = admitted && support.supported === true ? source?.modelConnectionTargetsPage : null;
  const invalid =
    !!page &&
    (page.page !== list.state.page ||
      page.pageSize !== list.state.pageSize ||
      page.items.some(
        (row) =>
          !row.environmentId ||
          !Number.isSafeInteger(row.environmentVersion) ||
          row.environmentVersion < 1 ||
          !Number.isSafeInteger(row.appVersion) ||
          row.appVersion < 1 ||
          typeof row.eligible !== "boolean" ||
          !["AUTO", "REQUEST", "DENY"].includes(row.action)
      ));
  const error =
    orgError?.message ??
    userError?.message ??
    (invalid ? t("changed") : (query.error?.message ?? null));
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  useEffect(() => {
    let saved: Recovery | null = null;
    try {
      const item = sessionStorage.getItem(storageKey);
      if (item) {
        const value = JSON.parse(item) as Recovery;
        if (
          value.review?.organizationId === model.organizationId &&
          value.review.modelId === model.id &&
          value.review.clusterId === model.clusterId &&
          value.review.providerId === model.providerId &&
          isConnectionGuid(value.request?.idempotencyKey) &&
          value.review.action === "REQUEST" &&
          /^[a-z][a-z0-9_]{0,31}$/.test(value.review.alias) &&
          typeof value.review.policyVersion === "string" &&
          !!value.review.policyVersion &&
          [
            value.review.modelVersion,
            value.review.environmentVersion,
            value.review.appVersion,
          ].every((version) => Number.isSafeInteger(version) && version > 0) &&
          value.request.organizationId === value.review.organizationId &&
          value.request.modelDeploymentId === value.review.modelId &&
          value.request.expectedClusterId === value.review.clusterId &&
          value.request.expectedProviderId === value.review.providerId &&
          value.request.appEnvironmentId === value.review.environmentId &&
          value.request.alias === value.review.alias &&
          value.request.ifMatchVersion === value.review.modelVersion &&
          value.request.ifMatchAppVersion === value.review.appVersion &&
          value.request.ifMatchEnvironmentVersion === value.review.environmentVersion &&
          value.request.policyVersion === value.review.policyVersion &&
          typeof value.appSlug === "string"
        )
          saved = value;
      }
    } catch {
      /* Local recovery is optional; absence never grants an action. */
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sessionStorage is the external recovery source
    setRecovery(saved);
  }, [storageKey, model.organizationId, model.id, model.clusterId, model.providerId]);
  function persist(value: Recovery | null) {
    setRecovery(value);
    try {
      if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
      else sessionStorage.removeItem(storageKey);
    } catch {
      /* The current exact request key remains in memory. */
    }
  }
  const epoch = useConnectionEpoch([
    scopeKey,
    model.version,
    admitted,
    support.supported,
    support.error,
    list.state,
    page?.items,
    query.loading,
    error,
  ]);
  const ownerEpoch = useConnectionEpoch([
    scopeKey,
    model.version,
    admitted,
    support.supported,
    support.error,
  ]);
  const busy = useRef(false);
  const [request] = useMutation<
    RequestModelConnectionMutation,
    RequestModelConnectionMutationVariables
  >(REQUEST_MODEL_CONNECTION, { fetchPolicy: "no-cache" });
  const [subscribe] = useMutation<
    SubscribeClusterModelMutation,
    SubscribeClusterModelMutationVariables
  >(SUBSCRIBE_CLUSTER_MODEL, { fetchPolicy: "no-cache" });
  const rows: ConnectionDestination[] = invalid
    ? []
    : (page?.items ?? []).map((row) => ({
        id: row.environmentId,
        version: row.environmentVersion,
        appVersion: row.appVersion,
        clusterId: row.clusterId,
        appSlug: row.appSlug,
        environmentName: row.environmentName,
        eligible: row.eligible,
        action: row.action,
        policyVersion: row.policyVersion ?? null,
        reason: row.reason ?? null,
      }));
  async function submit(review: ConnectionIntakeReview): Promise<ConnectionWriteResult> {
    const current = epoch();
    const selected = rows.find(
      (row) =>
        row.id === review.environmentId &&
        row.version === review.environmentVersion &&
        row.appVersion === review.appVersion &&
        row.action === review.action &&
        row.policyVersion === review.policyVersion &&
        row.eligible
    );
    if (
      !current() ||
      busy.current ||
      !admitted ||
      support.supported !== true ||
      support.error ||
      query.loading ||
      error ||
      !selected ||
      review.organizationId !== model.organizationId ||
      review.modelId !== model.id ||
      review.modelVersion !== model.version ||
      review.clusterId !== model.clusterId ||
      review.providerId !== model.providerId ||
      selected.clusterId !== model.clusterId ||
      !/^[a-z][a-z0-9_]{0,31}$/.test(review.alias)
    )
      return { accepted: false, message: t("changed") };
    if (recovery && JSON.stringify(recovery.review) !== JSON.stringify(review))
      return { accepted: false, message: t("recoveryAvailable") };
    busy.current = true;
    let sent = false;
    try {
      const decision = await client.query<
        GetModelConnectionActionQuery,
        GetModelConnectionActionQueryVariables
      >({
        query: GET_MODEL_CONNECTION_ACTION,
        variables: {
          input: {
            organizationId: review.organizationId,
            modelDeploymentId: review.modelId,
            expectedClusterId: review.clusterId,
            expectedProviderId: review.providerId,
            appEnvironmentId: review.environmentId,
          },
        },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      });
      if (!current()) return { accepted: false, message: t("changed") };
      const action = decision.data?.modelConnectionAction;
      if (
        !action ||
        action.action !== review.action ||
        action.policyVersion !== review.policyVersion
      )
        return { accepted: false, message: action?.reason || t("changed") };
      const input = {
        organizationId: review.organizationId,
        modelDeploymentId: review.modelId,
        expectedClusterId: review.clusterId,
        expectedProviderId: review.providerId,
        appEnvironmentId: review.environmentId,
        alias: review.alias,
        ifMatchVersion: review.modelVersion,
        ifMatchEnvironmentVersion: review.environmentVersion,
      };
      if (review.action === "REQUEST") {
        const pending = recovery ?? {
          review,
          appSlug: selected.appSlug,
          request: {
            ...input,
            ifMatchAppVersion: review.appVersion,
            policyVersion: review.policyVersion,
            idempotencyKey: crypto.randomUUID(),
          },
        };
        persist(pending);
        sent = true;
        const reply = (await request({ variables: { input: pending.request } })).data
          ?.requestModelConnection;
        if (!current()) return { accepted: false, message: t("changed") };
        if (reply?.ok !== true) {
          const refusal =
            reply?.ok === false &&
            Array.isArray(reply.errors) &&
            typeof reply.errors[0]?.code === "string" &&
            typeof reply.errors[0]?.message === "string";
          if (refusal) persist(null);
          return { accepted: false, message: refusal ? reply.errors[0].message : t("uncertain") };
        }
        if (!Array.isArray(reply.errors) || reply.errors.length !== 0)
          return { accepted: true, kind: "REQUEST", correlated: false };
        const data = reply.data;
        if (
          !data ||
          !isConnectionGuid(data.id) ||
          (data.subscriptionId != null && !isConnectionGuid(data.subscriptionId)) ||
          data.organizationId !== review.organizationId ||
          data.modelDeploymentId !== review.modelId ||
          data.appEnvironmentId !== review.environmentId ||
          data.clusterId !== review.clusterId ||
          data.providerId !== review.providerId ||
          data.alias !== review.alias ||
          !Number.isSafeInteger(data.version) ||
          data.version < 1
        )
          return { accepted: true, kind: "REQUEST", correlated: false };
        persist(null);
        return {
          accepted: true,
          kind: "REQUEST",
          id: data.id,
          version: data.version,
          subscriptionId: data.subscriptionId,
          correlated: true,
        };
      }
      sent = true;
      const reply = (await subscribe({ variables: { input } })).data?.subscribeClusterModel;
      if (!current()) return { accepted: false, message: t("changed") };
      if (!reply?.ok)
        return { accepted: false, message: reply?.errors[0]?.message ?? t("connectionUncertain") };
      const data = reply.data;
      const correlated =
        data?.deployment && data?.subscription
          ? subscriptionModelResult(
              reply,
              {
                organizationId: review.organizationId,
                modelId: review.modelId,
                modelVersion: review.modelVersion,
                expectedClusterId: review.clusterId,
                expectedProviderId: review.providerId,
                environmentId: review.environmentId,
                environmentVersion: review.environmentVersion,
                alias: review.alias,
              },
              t("connectionUncertain")
            )
          : null;
      if (!correlated?.accepted) return { accepted: true, kind: "AUTO", correlated: false };
      return {
        accepted: true,
        kind: "AUTO",
        id: data!.subscription.id,
        version: data!.subscription.version,
        correlated: true,
      };
    } catch {
      return {
        accepted: false,
        message: current()
          ? t(
              sent
                ? review.action === "REQUEST"
                  ? "uncertain"
                  : "connectionUncertain"
                : "requestFailed"
            )
          : t("changed"),
      };
    } finally {
      busy.current = false;
    }
  }
  return {
    scopeKey,
    deployment: {
      id: model.id,
      organizationId: model.organizationId,
      version: model.version,
      name: model.name,
      runtimeAdmission:
        model.runtimeSupported === true
          ? "configured"
          : model.runtimeSupported === false
            ? "unsupported"
            : "unknown",
      clusterId: model.clusterId,
      providerId: model.providerId,
      subscriptionsEnabled: model.subscriptionsEnabled,
    },
    supported: support.supported,
    supportError: support.error,
    onRetrySupport: support.onRetry,
    targets: {
      list,
      rows,
      loading: orgLoading || userLoading || (query.loading && !page),
      stale: blocked || !admitted || (!!page && (query.loading || !!error)),
      error: error ? { message: error } : null,
      totalCount: page?.totalCount ?? null,
      nextCursor: null,
      onRetry: () => {
        if (admitted && support.supported === true) void query.refetch().catch(() => {});
      },
    },
    recovery: recovery
      ? { environmentId: recovery.review.environmentId, alias: recovery.review.alias }
      : null,
    onRestoreRecovery: () => {
      if (!recovery) return null;
      list.setSearch(recovery.appSlug);
      return { environmentId: recovery.review.environmentId, alias: recovery.review.alias };
    },
    onDiscardRecovery: () => persist(null),
    onSubmit: submit,
    onAccepted: async () => {
      const current = ownerEpoch();
      if (!current() || !admitted) return;
      await query.refetch();
      if (current()) await onRefreshDeployment();
    },
  };
}
