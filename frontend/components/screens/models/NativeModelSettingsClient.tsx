"use client";
import { useMemo, useRef, useState } from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useLocalListState } from "@/components/list/use-list-state";
import type { ListDefinition } from "@/components/list/list-state";
import { GET_BEDROCK_MODEL_SUPPORT } from "@/graphql/models/native-models.queries";
import {
  UPDATE_BEDROCK_MODEL,
  UNREGISTER_BEDROCK_MODEL,
} from "@/graphql/models/native-models.mutations";
import {
  GET_CLUSTER_MODEL_DEPLOYMENT,
  LIST_MODEL_DEDICATED_APPS,
} from "@/graphql/models/shared-models.queries";
import type {
  ClusterModelFieldsFragment,
  GetBedrockModelSupportQuery,
  GetClusterModelDeploymentQuery,
  ListModelDedicatedAppsQuery,
  UpdateBedrockModelConnectionInput,
  UpdateBedrockModelMutation,
  UnregisterBedrockModelMutation,
} from "@/graphql/__generated__/operations";
import {
  NativeModelSettingsPanel,
  type NativeModelSettingsPanelProps,
} from "./NativeModelSettingsPanel";
import { isConnectionGuid, useConnectionEpoch } from "./use-model-connection-context";
import { modelAccessDraft } from "./shared-model-settings";
import { sameNativeSource, modelSourceMode } from "./native-model-source";
import { modelWriteFailure } from "./shared-model-write-results";
type Props = {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefresh: () => void;
  onRemoved?: () => void;
  onQueued?: (receipt: ClusterModelFieldsFragment) => void;
  queuedConfirmed?: boolean;
};
const options = { fetchPolicy: "no-cache" as const, context: { queryDeduplication: false } };
export function NativeModelSettingsClient(props: Props) {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return (
    <Context
      key={`${org?.id}:${user?.id}:${props.model.id}:${props.model.version}:${props.model.clusterId}:${props.model.providerId}`}
      {...props}
    />
  );
}
function Context({ model, blocked, onRefresh, onRemoved, onQueued, queuedConfirmed }: Props) {
  const t = useTranslations("models.native.connect"),
    inventory = useTranslations("models.shared.inventory");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg(),
    { user, loading: actorLoading, error: actorError } = useMe();
  const client = useApolloClient();
  const skip =
    orgLoading ||
    actorLoading ||
    !!orgError ||
    !!actorError ||
    !user?.id ||
    org?.id !== model.organizationId;
  const support = useQuery<GetBedrockModelSupportQuery>(GET_BEDROCK_MODEL_SUPPORT, {
    ...options,
    variables: { organizationId: model.organizationId },
    skip,
  });
  const allowed =
    !skip &&
    !blocked &&
    !support.loading &&
    !support.error &&
    support.data?.bedrockModelConnectionSupport?.enabled === true &&
    support.data.bedrockModelConnectionSupport.allowed === true &&
    modelSourceMode(model) === "native" &&
    [model.organizationId, model.id, model.clusterId, model.providerId].every(isConnectionGuid) &&
    Number.isSafeInteger(model.version) &&
    model.version > 0 &&
    ["active", "failed"].includes(model.status);
  const [name, setName] = useState(model.name),
    [subscriptions, setSubscriptions] = useState(model.subscriptionsEnabled);
  const initial = modelAccessDraft(model),
    [mode, setMode] = useState(initial.mode),
    [app, setApp] = useState(initial.app);
  const list = useLocalListState(
    useMemo<ListDefinition>(
      () => ({
        id: "nativeModelSettingsApps",
        fields: [],
        searchPlaceholder: inventory("appSearch"),
        views: [{ key: "all", label: inventory("selectApp"), filters: {} }],
        defaultSort: [],
        paging: "numbered",
        pageSizes: [10, 25, 50],
        defaultPageSize: 25,
      }),
      [inventory]
    )
  );
  const apps = useQuery<ListModelDedicatedAppsQuery>(LIST_MODEL_DEDICATED_APPS, {
    ...options,
    variables: {
      organizationId: model.organizationId,
      clusterId: model.clusterId,
      expectedProviderId: model.providerId,
      search: list.state.q || null,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    skip: !allowed || mode !== "DEDICATED",
  });
  const page = apps.data?.clusterModelDedicatedAppsPage;
  const pageValid =
    page &&
    page.page === list.state.page &&
    page.pageSize === list.state.pageSize &&
    page.items.every(
      (row) => isConnectionGuid(row.id) && Number.isSafeInteger(row.version) && row.version > 0
    );
  const immutable = {
    organizationId: model.organizationId,
    id: model.id,
    expectedClusterId: model.clusterId,
    expectedProviderId: model.providerId,
    ifMatchVersion: model.version,
  };
  const input: UpdateBedrockModelConnectionInput = {
    ...immutable,
    name: name.trim(),
    allowSubscriptions: subscriptions,
    sharingMode: mode,
    dedicatedAppId: mode === "DEDICATED" ? (app?.id ?? null) : null,
    ifMatchDedicatedAppVersion: mode === "DEDICATED" ? (app?.version ?? null) : null,
  };
  const inputValid =
    !!input.name &&
    input.name.length <= 128 &&
    !/[\u0000-\u001f\u007f]/.test(input.name) &&
    (mode === "SHARED" ||
      (pageValid &&
        !apps.loading &&
        !apps.error &&
        app &&
        page.items.some((row) => row.id === app.id && row.version === app.version)));
  const epoch = useConnectionEpoch([org?.id, user?.id, allowed, blocked, model, input]);
  const owner = useConnectionEpoch([org?.id, user?.id, model.id, model.version]);
  const [review, setReview] = useState<{
    kind: "update" | "remove";
    key: string;
    current: () => boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false),
    [sent, setSent] = useState(false),
    [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<"queued" | "removed" | null>(null),
    busyRef = useRef(false);
  const currentReview =
    !!review &&
    review.current() &&
    review.key === JSON.stringify(input) &&
    allowed &&
    !busy &&
    !sent &&
    (review.kind !== "update" || !!inputValid);
  const props: NativeModelSettingsPanelProps = {
    allowed,
    loading: orgLoading || actorLoading || (!skip && support.loading),
    reason:
      orgError?.message ??
      actorError?.message ??
      support.error?.message ??
      support.data?.bedrockModelConnectionSupport?.reason ??
      (modelSourceMode(model) === "native_unavailable" ? (model.reason ?? t("changed")) : null) ??
      (!allowed && !support.loading ? t("changed") : null),
    onRetry: () => {
      if (!skip) void support.refetch().catch(() => {});
    },
    name,
    onName: (value) => {
      if (!busy && !sent) setName(value);
    },
    subscriptions,
    onSubscriptions: (value) => {
      if (!busy && !sent) setSubscriptions(value);
    },
    mode,
    onMode: (value) => {
      if (!busy && !sent) {
        setMode(value);
        setApp(null);
      }
    },
    app,
    apps: {
      list,
      rows: allowed && pageValid && !apps.error ? page.items : [],
      totalCount: pageValid ? (page.totalCount ?? null) : null,
      nextCursor: null,
      loading: apps.loading,
      stale: apps.loading || busy,
      error: apps.error
        ? { message: apps.error.message }
        : page && !pageValid
          ? { message: t("changed") }
          : null,
      onRetry: () => {
        if (allowed && mode === "DEDICATED") void apps.refetch().catch(() => {});
      },
    },
    onApp: (value) => {
      if (
        !busy &&
        !sent &&
        allowed &&
        pageValid &&
        !apps.loading &&
        !apps.error &&
        page.items.some((row) => row.id === value.id && row.version === value.version)
      )
        setApp(value);
    },
    canUpdate: !!inputValid,
    busy,
    sent,
    outcome: outcome ?? (queuedConfirmed ? "queued" : null),
    error,
    review: review?.kind ?? null,
    canConfirm: currentReview,
    onReview: (kind) => {
      if (allowed && !busy && !sent && (kind !== "update" || inputValid)) {
        setError(null);
        setReview({ kind, key: JSON.stringify(input), current: epoch() });
      }
    },
    onDismiss: () => {
      if (!busy) setReview(null);
    },
    onConfirm: async () => {
      if (!review || !currentReview || busyRef.current) return false;
      const current = review.current,
        currentOwner = owner(),
        kind = review.kind;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      try {
        const permission = (
          await client.query<GetBedrockModelSupportQuery>({
            ...options,
            query: GET_BEDROCK_MODEL_SUPPORT,
            variables: { organizationId: model.organizationId },
          })
        ).data?.bedrockModelConnectionSupport;
        if (!current()) return false;
        if (permission?.enabled !== true || permission.allowed !== true)
          throw new Error(permission?.reason ?? t("changed"));
        const fresh = (
          await client.query<GetClusterModelDeploymentQuery>({
            ...options,
            query: GET_CLUSTER_MODEL_DEPLOYMENT,
            variables: { organizationId: model.organizationId, id: model.id },
          })
        ).data?.clusterModelDeployment;
        if (!current()) return false;
        if (
          !fresh ||
          fresh.id !== model.id ||
          fresh.organizationId !== model.organizationId ||
          fresh.clusterId !== model.clusterId ||
          fresh.providerId !== model.providerId ||
          fresh.version !== model.version ||
          !["active", "failed"].includes(fresh.status) ||
          modelSourceMode(fresh) !== "native" ||
          !sameNativeSource(fresh.nativeSource, model.nativeSource)
        )
          throw new Error(t("changed"));
        setSent(true);
        const envelope =
          kind === "update"
            ? (
                await client.mutate<UpdateBedrockModelMutation>({
                  mutation: UPDATE_BEDROCK_MODEL,
                  variables: { input },
                  fetchPolicy: "no-cache",
                })
              ).data?.updateBedrockModelConnection
            : (
                await client.mutate<UnregisterBedrockModelMutation>({
                  mutation: UNREGISTER_BEDROCK_MODEL,
                  variables: { input: immutable },
                  fetchPolicy: "no-cache",
                })
              ).data?.unregisterBedrockModelConnection;
        if (!current()) return false;
        const failure = modelWriteFailure(envelope, t("unconfirmed")),
          data = envelope?.data;
        if (failure) {
          setError(failure);
          if (envelope?.ok === false) setSent(false);
          return false;
        }
        if (
          !data ||
          data.id !== model.id ||
          data.organizationId !== model.organizationId ||
          data.clusterId !== model.clusterId ||
          data.providerId !== model.providerId ||
          modelSourceMode(data) !== "native" ||
          !sameNativeSource(data.nativeSource, model.nativeSource) ||
          data.ready != null ||
          data.runtimeSupported != null ||
          (kind === "update"
            ? data.version <= model.version ||
              data.status !== "updating" ||
              !isConnectionGuid(data.operationId) ||
              data.operationCompletedAt != null ||
              data.desiredSubscriptionRevision <= model.desiredSubscriptionRevision ||
              data.name !== input.name ||
              data.subscriptionsEnabled !== input.allowSubscriptions ||
              data.sharingMode !== input.sharingMode ||
              data.dedicatedAppId !== input.dedicatedAppId ||
              (mode === "DEDICATED" &&
                data.dedicatedAppVersion !== input.ifMatchDedicatedAppVersion)
            : data.version !== model.version)
        ) {
          setError(t("unconfirmed"));
          return false;
        }
        setOutcome(kind === "update" ? "queued" : "removed");
        try {
          if (kind === "remove") onRemoved?.();
          else onQueued?.(data);
          onRefresh();
        } catch {
          /* Read failure cannot undo an accepted write. */
        }
        return true;
      } catch (cause) {
        if (current()) setError(cause instanceof Error ? cause.message : t("unconfirmed"));
        return false;
      } finally {
        busyRef.current = false;
        if (currentOwner()) setBusy(false);
      }
    },
  };
  return <NativeModelSettingsPanel {...props} />;
}
