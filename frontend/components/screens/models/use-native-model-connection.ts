"use client";

import { useMemo, useRef, useState } from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useLocalListState } from "@/components/list/use-list-state";
import type { ListDefinition } from "@/components/list/list-state";
import {
  GET_BEDROCK_MODEL_SUPPORT,
  GET_BEDROCK_MODEL_ACTION,
  GET_BEDROCK_MODEL_SOURCE,
  LIST_BEDROCK_MODEL_SOURCES,
  LIST_NATIVE_MODEL_CLUSTERS,
} from "@/graphql/models/native-models.queries";
import { REGISTER_BEDROCK_MODEL } from "@/graphql/models/native-models.mutations";
import { LIST_MODEL_DEDICATED_APPS } from "@/graphql/models/shared-models.queries";
import type {
  BedrockModelPlacementInput,
  BedrockModelSourceKind,
  BedrockModelSourceFieldsFragment,
  GetBedrockModelSupportQuery,
  GetBedrockModelSupportQueryVariables,
  GetBedrockModelActionQuery,
  GetBedrockModelActionQueryVariables,
  GetBedrockModelSourceQuery,
  GetBedrockModelSourceQueryVariables,
  ListBedrockModelSourcesQuery,
  ListBedrockModelSourcesQueryVariables,
  ListNativeModelClustersQuery,
  ListNativeModelClustersQueryVariables,
  ListModelDedicatedAppsQuery,
  ListModelDedicatedAppsQueryVariables,
  RegisterBedrockModelConnectionInput,
  RegisterBedrockModelMutation,
  RegisterBedrockModelMutationVariables,
} from "@/graphql/__generated__/operations";
import { isConnectionGuid, useConnectionEpoch } from "./use-model-connection-context";
import {
  nativeRegistrationResult,
  sameNativeSource,
  validNativeSource,
} from "./native-model-source";
import type { NativeModelConnectScreenProps, NativeModelCluster } from "./NativeModelConnectScreen";

const exactPlacement = (input: BedrockModelPlacementInput): BedrockModelPlacementInput => ({
  organizationId: input.organizationId,
  clusterId: input.clusterId,
  expectedProviderId: input.expectedProviderId,
  expectedClusterVersion: input.expectedClusterVersion,
  expectedProviderVersion: input.expectedProviderVersion,
});
const readOptions = { fetchPolicy: "no-cache" as const, context: { queryDeduplication: false } };
const listDefinition = (id: string, label: string): ListDefinition => ({
  id,
  fields: [],
  searchPlaceholder: label,
  views: [{ key: "all", label, filters: {} }],
  defaultSort: [],
  paging: "numbered",
  pageSizes: [10, 25, 50],
  defaultPageSize: 25,
});

export const useNativeModelConnection = (): NativeModelConnectScreenProps => {
  const t = useTranslations("models.native.connect"),
    inventory = useTranslations("models.shared.inventory");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const { user, loading: actorLoading, error: actorError } = useMe();
  const client = useApolloClient();
  const skipped = orgLoading || actorLoading || !!orgError || !!actorError || !org?.id || !user?.id;
  const support = useQuery<GetBedrockModelSupportQuery, GetBedrockModelSupportQueryVariables>(
    GET_BEDROCK_MODEL_SUPPORT,
    {
      ...readOptions,
      variables: { organizationId: org?.id ?? "" },
      skip: skipped,
    }
  );
  const allowed =
    !skipped &&
    !support.loading &&
    !support.error &&
    support.data?.bedrockModelConnectionSupport?.enabled === true &&
    support.data.bedrockModelConnectionSupport.allowed === true;
  const clustersList = useLocalListState(
    useMemo(() => listDefinition("nativeModelPlacement", t("clusterSearch")), [t])
  );
  const clusters = useQuery<ListNativeModelClustersQuery, ListNativeModelClustersQueryVariables>(
    LIST_NATIVE_MODEL_CLUSTERS,
    {
      ...readOptions,
      variables: {
        organizationId: org?.id ?? "",
        search: clustersList.state.q || null,
        page: clustersList.state.page,
        pageSize: clustersList.state.pageSize,
      },
      skip: !allowed,
    }
  );
  const [step, setStep] = useState<1 | 2 | 3>(1),
    [cluster, setCluster] = useState<NativeModelCluster | null>(null);
  const [kind, setKind] = useState<BedrockModelSourceKind>("FOUNDATION_MODEL"),
    [lookup, setLookup] = useState("");
  const [catalogue, setCatalogue] = useState<
    ListBedrockModelSourcesQuery["bedrockModelSources"] | null
  >(null);
  const [detail, setDetail] = useState<BedrockModelSourceFieldsFragment | null>(null);
  const [name, setName] = useState(""),
    [subscriptions, setSubscriptions] = useState(true);
  const [mode, setMode] = useState<"SHARED" | "DEDICATED">("SHARED");
  const [app, setApp] = useState<
    ListModelDedicatedAppsQuery["clusterModelDedicatedAppsPage"]["items"][number] | null
  >(null);
  const [review, setReview] = useState<RegisterBedrockModelConnectionInput | null>(null);
  const [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false),
    [registeredId, setRegisteredId] = useState<string | null>(null);
  const busyRef = useRef(false);
  const appsList = useLocalListState(
    useMemo(() => listDefinition("nativeDedicatedApps", inventory("appSearch")), [inventory])
  );
  const apps = useQuery<ListModelDedicatedAppsQuery, ListModelDedicatedAppsQueryVariables>(
    LIST_MODEL_DEDICATED_APPS,
    {
      ...readOptions,
      variables: {
        organizationId: org?.id ?? "",
        clusterId: cluster?.id ?? "",
        expectedProviderId: cluster?.providerId ?? "",
        search: appsList.state.q || null,
        page: appsList.state.page,
        pageSize: appsList.state.pageSize,
      },
      skip: !allowed || !cluster || mode !== "DEDICATED",
    }
  );
  const owner = useConnectionEpoch([org?.id, user?.id, skipped]);
  const epoch = useConnectionEpoch([
    org?.id,
    user?.id,
    skipped,
    allowed,
    step,
    cluster,
    kind,
    lookup,
    detail?.identity.sourceFingerprint,
    name,
    subscriptions,
    mode,
    app,
    review,
  ]);
  const placement = (target = cluster): BedrockModelPlacementInput | null =>
    target && org?.id
      ? {
          organizationId: org.id,
          clusterId: target.id,
          expectedProviderId: target.providerId,
          expectedClusterVersion: target.version,
          expectedProviderVersion: target.providerVersion,
        }
      : null;
  const validCluster = (row: NativeModelCluster) =>
    isConnectionGuid(row.id) &&
    isConnectionGuid(row.providerId) &&
    Number.isSafeInteger(row.version) &&
    row.version > 0 &&
    Number.isSafeInteger(row.providerVersion) &&
    row.providerVersion > 0;
  const currentPlacement = async (input: BedrockModelPlacementInput) => {
    const data = (
      await client.query<GetBedrockModelActionQuery, GetBedrockModelActionQueryVariables>({
        ...readOptions,
        query: GET_BEDROCK_MODEL_ACTION,
        variables: { input: exactPlacement(input) },
      })
    ).data?.bedrockModelConnectionAction;
    if (data?.enabled !== true || data.allowed !== true)
      throw new Error(data?.reason ?? t("changed"));
  };
  const sourceRead = async (input: BedrockModelPlacementInput, identifier: string) => {
    const source = (
      await client.query<GetBedrockModelSourceQuery, GetBedrockModelSourceQueryVariables>({
        ...readOptions,
        query: GET_BEDROCK_MODEL_SOURCE,
        variables: {
          input: { ...exactPlacement(input), sourceKind: kind, sourceIdentifier: identifier },
        },
      })
    ).data?.bedrockModelSource;
    if (
      !source ||
      !validNativeSource(source.identity) ||
      source.identity.sourceKind !== kind ||
      ![source.identity.sourceId, source.identity.sourceArn].includes(identifier)
    )
      throw new Error(t("changed"));
    return source;
  };
  const command = async (body: (current: () => boolean) => Promise<void>) => {
    if (!allowed || busyRef.current || sent) return;
    const current = epoch(),
      currentOwner = owner();
    if (!current()) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await body(current);
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : t("changed"));
    } finally {
      busyRef.current = false;
      if (currentOwner()) setBusy(false);
    }
  };
  const appPage = apps.data?.clusterModelDedicatedAppsPage;
  const appsValid =
    appPage &&
    appPage.page === appsList.state.page &&
    appPage.pageSize === appsList.state.pageSize &&
    appPage.items.every(
      (row) => isConnectionGuid(row.id) && Number.isSafeInteger(row.version) && row.version > 0
    ) &&
    new Set(appPage.items.map((row) => row.id)).size === appPage.items.length;
  const registerInput = (): RegisterBedrockModelConnectionInput | null => {
    const target = placement();
    if (
      !target ||
      !detail?.registerable ||
      !validNativeSource(detail.identity) ||
      detail.identity.configurationState !== "configured" ||
      !name.trim() ||
      name.trim().length > 128 ||
      /[\u0000-\u001f\u007f]/.test(name) ||
      (mode === "DEDICATED" &&
        (!app ||
          !appsValid ||
          !appPage.items.some((row) => row.id === app.id && row.version === app.version) ||
          apps.loading ||
          apps.error))
    )
      return null;
    return {
      ...target,
      sourceKind: kind,
      sourceIdentifier: detail.identity.sourceId,
      sourceFingerprint: detail.identity.sourceFingerprint,
      name: name.trim(),
      allowSubscriptions: subscriptions,
      sharingMode: mode,
      dedicatedAppId: mode === "DEDICATED" ? app!.id : null,
      ifMatchDedicatedAppVersion: mode === "DEDICATED" ? app!.version : null,
    };
  };
  const page = clusters.data?.clusterModelPlacementClustersPage;
  const pageValid =
    page &&
    page.page === clustersList.state.page &&
    page.pageSize === clustersList.state.pageSize &&
    page.items.every(validCluster);
  return {
    step,
    allowed,
    supportLoading: orgLoading || actorLoading || (!skipped && support.loading),
    supportReason:
      orgError?.message ??
      actorError?.message ??
      support.error?.message ??
      support.data?.bedrockModelConnectionSupport?.reason ??
      null,
    onRetrySupport: () => {
      if (!skipped) void support.refetch().catch(() => {});
    },
    cluster,
    clusters: {
      list: clustersList,
      rows: allowed && pageValid ? page.items : [],
      totalCount: pageValid ? (page.totalCount ?? null) : null,
      nextCursor: null,
      loading: allowed && clusters.loading,
      stale: busy || clusters.loading,
      error: clusters.error
        ? { message: clusters.error.message }
        : page && !pageValid
          ? { message: t("changed") }
          : null,
      onRetry: () => {
        if (allowed) void clusters.refetch().catch(() => {});
      },
    },
    onSelectCluster: (row) =>
      command(async (current) => {
        if (
          !validCluster(row) ||
          !pageValid ||
          clusters.loading ||
          clusters.error ||
          !page.items.some((choice) => JSON.stringify(choice) === JSON.stringify(row))
        )
          throw new Error(t("changed"));
        const input = placement(row)!;
        await currentPlacement(input);
        if (current()) {
          setCluster(row);
          setDetail(null);
          setCatalogue(null);
          setReview(null);
          setLookup("");
          setStep(2);
        }
      }),
    kind,
    onKind: (value) => {
      if (!busy && !sent) {
        setKind(value);
        setCatalogue(null);
        setDetail(null);
        setReview(null);
        setLookup("");
      }
    },
    lookup,
    onLookup: (value) => {
      if (!busy && !sent) {
        setLookup(value);
        setDetail(null);
        setReview(null);
      }
    },
    catalogue,
    detail,
    busy,
    error,
    sent,
    registeredId,
    onLoad: () =>
      command(async (current) => {
        const input = placement();
        if (!input) return;
        await currentPlacement(input);
        if (!current()) return;
        const data = (
          await client.query<ListBedrockModelSourcesQuery, ListBedrockModelSourcesQueryVariables>({
            ...readOptions,
            query: LIST_BEDROCK_MODEL_SOURCES,
            variables: { input, sourceKind: kind, limit: 100 },
          })
        ).data?.bedrockModelSources;
        if (
          !data ||
          !["metadata", "denied", "not_found", "error", "refused"].includes(data.state) ||
          data.items.length > 100 ||
          data.items.some(
            (row) => !validNativeSource(row.identity) || row.identity.sourceKind !== kind
          ) ||
          new Set(data.items.map((row) => row.identity.sourceArn)).size !== data.items.length
        )
          throw new Error(t("changed"));
        if (current()) setCatalogue(data);
      }),
    onInspect: (identifier) =>
      command(async (current) => {
        const input = placement();
        if (
          !input ||
          !identifier.trim() ||
          identifier.length > 2048 ||
          /^https?:/i.test(identifier)
        )
          return;
        await currentPlacement(input);
        if (!current()) return;
        const source = await sourceRead(input, identifier.trim());
        if (current()) {
          setDetail(source);
          setLookup(source.identity.sourceId);
          setName(source.name.slice(0, 128));
          setReview(null);
        }
      }),
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
      list: appsList,
      rows: allowed && appsValid && !apps.error ? appPage.items : [],
      totalCount: appsValid ? (appPage.totalCount ?? null) : null,
      nextCursor: null,
      loading: apps.loading,
      stale: busy || apps.loading,
      error: apps.error
        ? { message: apps.error.message }
        : appPage && !appsValid
          ? { message: t("changed") }
          : null,
      onRetry: () => {
        if (allowed && cluster && mode === "DEDICATED") void apps.refetch().catch(() => {});
      },
    },
    onApp: (row) => {
      if (
        !busy &&
        !sent &&
        !apps.loading &&
        !apps.error &&
        appsValid &&
        appPage.items.some((item) => item.id === row.id && item.version === row.version)
      )
        setApp(row);
    },
    review,
    canReview: !!registerInput() && allowed && !busy && !sent,
    onReview: () =>
      command(async (current) => {
        const input = registerInput();
        if (!input || !detail) return;
        await currentPlacement(input);
        if (!current()) return;
        const fresh = await sourceRead(input, input.sourceIdentifier);
        if (!fresh.registerable || !sameNativeSource(detail.identity, fresh.identity))
          throw new Error(t("changed"));
        if (current()) {
          setDetail(fresh);
          setReview(input);
          setStep(3);
        }
      }),
    onRegister: () =>
      command(async (current) => {
        const input = registerInput();
        if (!input || !review || !detail || JSON.stringify(input) !== JSON.stringify(review))
          throw new Error(t("changed"));
        await currentPlacement(input);
        if (!current()) return;
        const fresh = await sourceRead(input, input.sourceIdentifier);
        if (!fresh.registerable || !sameNativeSource(detail.identity, fresh.identity))
          throw new Error(t("changed"));
        if (!current()) return;
        setSent(true);
        try {
          const envelope = (
            await client.mutate<
              RegisterBedrockModelMutation,
              RegisterBedrockModelMutationVariables
            >({
              mutation: REGISTER_BEDROCK_MODEL,
              variables: { input },
              fetchPolicy: "no-cache",
            })
          ).data?.registerBedrockModelConnection;
          if (!current()) return;
          const result = nativeRegistrationResult(
            envelope,
            input,
            fresh.identity,
            t("unconfirmed")
          );
          if (result.accepted) setRegisteredId(result.id);
          else {
            setError(result.message);
            if (envelope?.ok === false) setSent(false);
          }
        } catch {
          if (current()) setError(t("unconfirmed"));
        }
      }),
    onBack: () => {
      if (!busy && !sent) {
        setReview(null);
        setStep(step === 3 ? 2 : 1);
      }
    },
  };
};
