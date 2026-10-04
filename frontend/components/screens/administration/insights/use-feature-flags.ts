"use client";

import { useApolloClient, useMutation } from "@apollo/client/react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type {
  AdminFeatureInventoryQuery,
  SetFeatureFlagMutation,
  SetFeatureFlagMutationVariables,
} from "@/graphql/__generated__/operations";
import { SET_FEATURE_FLAG } from "@/graphql/server/server.mutations";
import { ADMIN_FEATURE_INVENTORY, SERVER_INFO } from "@/graphql/server/server.queries";
import { useMe } from "@/graphql/user/user.hooks";
import { GET_ME } from "@/graphql/user/user.queries";
import type { MeQueryData } from "@/graphql/user/user.types";

import { humanizeKey } from "./feature-key";

export type RuntimeFlag = AdminFeatureInventoryQuery["astroliftServerInfo"]["featureFlags"][number];
export type BuildTimeFeature =
  AdminFeatureInventoryQuery["astroliftServerInfo"]["buildTimeFeatures"][number];
export type FeatureRecovery = { kind: "accepted" | "uncertain"; key: string; enabled: boolean };

const inventoryValid = (data: AdminFeatureInventoryQuery | undefined) => {
  const flags = data?.astroliftServerInfo?.featureFlags;
  const build = data?.astroliftServerInfo?.buildTimeFeatures;
  return (
    Array.isArray(flags) &&
    Array.isArray(build) &&
    flags.every(
      (flag) =>
        !!flag &&
        typeof flag.key === "string" &&
        typeof flag.enabled === "boolean" &&
        (flag.description == null || typeof flag.description === "string")
    ) &&
    build.every(
      (feature) =>
        !!feature &&
        typeof feature.key === "string" &&
        typeof feature.enabled === "boolean" &&
        typeof feature.envVar === "string" &&
        (feature.description == null || typeof feature.description === "string")
    ) &&
    new Set(flags.map((flag) => flag.key)).size === flags.length &&
    new Set(build.map((feature) => feature.key)).size === build.length
  );
};

export function useFeatureFlags() {
  const t = useTranslations("administration.features");
  const client = useApolloClient();
  const { user, loading: viewerLoading, error: viewerError } = useMe();
  const viewer = `${user?.id ?? ""}:${viewerLoading}:${!!viewerError}`;
  const [scope, setScope] = React.useState({ viewer, client, epoch: 0 });
  if (scope.viewer !== viewer || scope.client !== client) {
    setScope({ viewer, client, epoch: scope.epoch + 1 });
  }
  const contextKey = scope.epoch;
  const identity = React.useRef(scope);
  const live = React.useRef(false);
  React.useLayoutEffect(() => {
    identity.current = scope;
  });
  React.useLayoutEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const [setFlag] = useMutation<SetFeatureFlagMutation, SetFeatureFlagMutationVariables>(
    SET_FEATURE_FLAG
  );
  const [snapshot, setSnapshot] = React.useState<{
    epoch: number;
    data?: AdminFeatureInventoryQuery;
    error: Error | null;
    loading: boolean;
    recovery: FeatureRecovery | null;
  }>({ epoch: contextKey, error: null, loading: true, recovery: null });
  const [pendingKey, setPendingKey] = React.useState<string | null>(null);
  const busy = React.useRef(false);
  const reads = React.useRef(0);
  const current = (epoch: number) => live.current && identity.current.epoch === epoch;
  const validViewer = !!user?.id && !viewerLoading && !viewerError;
  const state = snapshot.epoch === contextKey ? snapshot : null;
  const stateRef = React.useRef(state);
  React.useLayoutEffect(() => {
    stateRef.current = state;
  });

  const refresh = React.useCallback(
    async (epoch: number, navigation: boolean) => {
      const sequence = ++reads.current;
      if (!current(epoch)) return;
      setSnapshot((prior) =>
        prior.epoch === epoch
          ? { ...prior, loading: true, error: null }
          : { epoch, loading: true, error: null, recovery: null }
      );
      try {
        const result = await Promise.allSettled([
          client.query<AdminFeatureInventoryQuery>({
            query: ADMIN_FEATURE_INVENTORY,
            fetchPolicy: "no-cache",
            context: { queryDeduplication: false },
          }),
          ...(navigation
            ? [
                client.query({
                  query: SERVER_INFO,
                  fetchPolicy: "no-cache",
                  context: { queryDeduplication: false },
                }),
                client.query<MeQueryData>({
                  query: GET_ME,
                  fetchPolicy: "no-cache",
                  context: { queryDeduplication: false },
                }),
              ]
            : []),
        ]);
        if (!current(epoch) || sequence !== reads.current) return;
        const inventory = result[0];
        const recoveryKey = stateRef.current?.recovery?.key;
        const succeeded =
          inventory.status === "fulfilled" &&
          inventoryValid(inventory.value.data) &&
          (!recoveryKey ||
            inventory.value.data?.astroliftServerInfo.featureFlags.some(
              (flag) => flag.key === recoveryKey
            )) &&
          result.every((read) => read.status === "fulfilled");
        const me = navigation ? result[2] : null;
        if (me?.status === "fulfilled" && (me.value.data as MeQueryData)?.me?.id !== user?.id) {
          try {
            client.writeQuery({ query: GET_ME, data: { me: null } });
          } catch {
            // A failed cache invalidation cannot authorize another installation write.
          }
          setSnapshot((prior) => ({
            ...prior,
            loading: false,
            error: new Error(t("contextChanged")),
          }));
          return;
        }
        if (!succeeded) {
          setSnapshot((prior) => ({
            ...prior,
            loading: false,
            error: new Error(t("refreshError")),
          }));
          return;
        }
        if (inventory.status !== "fulfilled") return;
        try {
          if (navigation) {
            const nav = result[1];
            if (nav.status === "fulfilled")
              client.writeQuery({ query: SERVER_INFO, data: nav.value.data });
            if (me?.status === "fulfilled")
              client.writeQuery({ query: GET_ME, data: me.value.data });
          }
        } catch {
          setSnapshot((prior) => ({
            ...prior,
            loading: false,
            error: new Error(t("refreshError")),
          }));
          return;
        }
        setSnapshot({
          epoch,
          data: inventory.value.data as AdminFeatureInventoryQuery,
          loading: false,
          error: null,
          recovery: null,
        });
      } catch {
        if (current(epoch) && sequence === reads.current)
          setSnapshot((prior) => ({
            ...prior,
            loading: false,
            error: new Error(t("refreshError")),
          }));
      }
    },
    [client, t, user?.id]
  );

  React.useEffect(() => {
    if (validViewer && !stateRef.current?.data && !stateRef.current?.recovery) {
      void refresh(contextKey, false);
    }
  }, [contextKey, validViewer, refresh]);

  async function toggleFlag(flag: RuntimeFlag) {
    const before = stateRef.current;
    const actual = before?.data?.astroliftServerInfo.featureFlags.find(
      (item) => item.key === flag.key
    );
    if (
      busy.current ||
      !validViewer ||
      before?.loading ||
      before?.error ||
      before?.recovery ||
      !actual ||
      actual.enabled !== flag.enabled
    ) {
      throw new Error(t("readRequired"));
    }
    busy.current = true;
    const epoch = contextKey;
    const next = !flag.enabled;
    const intent: FeatureRecovery = { kind: "uncertain", key: flag.key, enabled: next };
    setPendingKey(flag.key);
    ++reads.current;
    let response: SetFeatureFlagMutation["setFeatureFlag"] | undefined;
    try {
      try {
        const res = await setFlag({
          variables: { key: flag.key, enabled: next },
          fetchPolicy: "no-cache",
        });
        response = res.data?.setFeatureFlag;
      } catch {
        if (!current(epoch)) throw new Error(t("contextChanged"));
      }
      if (!current(epoch)) throw new Error(t("contextChanged"));
      if (response?.ok === false)
        throw new Error(response.errors?.[0]?.message ?? t("updateError"));
      const accepted =
        response?.ok === true &&
        Array.isArray(response.errors) &&
        response.errors.length === 0 &&
        response.data?.key === flag.key &&
        response.data.enabled === next;
      setSnapshot((prior) => ({
        ...prior,
        recovery: { ...intent, kind: accepted ? "accepted" : "uncertain" },
      }));
      if (accepted) {
        toast.success(t(next ? "enabled" : "disabled", { key: humanizeKey(flag.key) }));
        void refresh(epoch, true);
      }
      // An uncertain response also closes the write prompt: only a new read may resolve it.
    } finally {
      busy.current = false;
      setPendingKey(null);
    }
  }

  const accepted = state?.recovery?.kind === "accepted" ? state.recovery : null;
  return {
    contextKey,
    runtimeFlags: (state?.data?.astroliftServerInfo.featureFlags ?? []).map((flag) =>
      accepted?.key === flag.key ? { ...flag, enabled: accepted.enabled } : flag
    ),
    buildTimeFeatures: state?.data?.astroliftServerInfo.buildTimeFeatures ?? [],
    loading: viewerLoading || (state?.loading ?? validViewer),
    error:
      state?.error ??
      viewerError ??
      (!viewerLoading && !user?.id ? new Error(t("contextChanged")) : null),
    onRetry: (): void => {
      if (!busy.current && validViewer) void refresh(contextKey, true);
    },
    pendingKey,
    controlsDisabled:
      !validViewer ||
      !state ||
      state.loading ||
      !!state.error ||
      !!state.recovery ||
      pendingKey !== null,
    recovery: state?.recovery ?? null,
    toggleFlag,
  };
}
