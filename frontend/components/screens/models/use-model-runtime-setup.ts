"use client";
import { useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import { GET_CLUSTER_MODEL_RUNTIME_SETTINGS } from "@/graphql/models/model-runtime-settings.queries";
import { UPDATE_CLUSTER_MODEL_RUNTIME } from "@/graphql/models/model-runtime-settings.mutations";
import type {
  GetClusterModelRuntimeSettingsQuery,
  GetClusterModelRuntimeSettingsQueryVariables,
  UpdateClusterModelRuntimeMutation,
  UpdateClusterModelRuntimeMutationVariables,
  ModelRuntimeMode,
} from "@/graphql/__generated__/operations";
import type { ModelRuntimeSetupProps, RuntimeObservation } from "./ModelRuntimeSetupPanel";
export function useModelRuntimeSetup(
  target: GetClusterModelRuntimeSettingsQueryVariables | null,
  scopeKey: string,
  allowed: boolean,
  mode: ModelRuntimeMode,
  refreshAdmission: () => unknown
): ModelRuntimeSetupProps {
  const query = useQuery<
    GetClusterModelRuntimeSettingsQuery,
    GetClusterModelRuntimeSettingsQueryVariables
  >(GET_CLUSTER_MODEL_RUNTIME_SETTINGS, {
    variables: target ?? { organizationId: "", clusterId: "", expectedProviderId: "" },
    skip: !target || !allowed,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const [save] = useMutation<
    UpdateClusterModelRuntimeMutation,
    UpdateClusterModelRuntimeMutationVariables
  >(UPDATE_CLUSTER_MODEL_RUNTIME, { fetchPolicy: "no-cache" });
  const binding = JSON.stringify([scopeKey, target, allowed, mode]);
  const [lease, setLease] = useState({ key: binding, revision: 0 });
  if (lease.key !== binding) setLease({ key: binding, revision: lease.revision + 1 });
  const latest = useRef({ key: binding, revision: lease.revision }),
    lifecycle = useRef(0),
    mounted = useRef(false);

  useLayoutEffect(() => {
    mounted.current = true;
    const epoch = lifecycle.current + 1;
    lifecycle.current = epoch;
    return () => {
      mounted.current = false;
      lifecycle.current = epoch + 1;
    };
  }, []);
  const source = query.error ? (query.data ?? query.previousData) : query.data;
  const observation = allowed && target ? (source?.clusterModelRuntimeSettings ?? null) : null;
  const snapshotKey = JSON.stringify([
    binding,
    observation?.clusterVersion,
    observation?.providerVersion,
    query.loading,
    Boolean(query.error),
  ]);
  useLayoutEffect(() => {
    latest.current = { key: snapshotKey, revision: lease.revision };
  }, [snapshotKey, lease.revision]);
  const matches = (
    value: Pick<RuntimeObservation, "organizationId" | "clusterId" | "providerId"> | null
  ) =>
    Boolean(
      target &&
      value &&
      value.organizationId === target.organizationId &&
      value.clusterId === target.clusterId &&
      value.providerId === target.expectedProviderId
    );
  return {
    scopeKey: binding,
    allowed,
    mode,
    observation: matches(observation) ? observation : null,
    loading: allowed && Boolean(target) && query.loading,
    error: query.error?.message ?? null,
    onRetry: () => {
      if (allowed && target) void query.refetch().catch(() => {});
    },
    onSave: async (declaration, reviewed) => {
      const epoch = lifecycle.current;
      const current = () =>
        mounted.current &&
        epoch === lifecycle.current &&
        latest.current.key === snapshotKey &&
        latest.current.revision === lease.revision;
      if (
        !current() ||
        !allowed ||
        !target ||
        !matches(reviewed) ||
        !observation ||
        reviewed.clusterVersion !== observation.clusterVersion ||
        reviewed.providerVersion !== observation.providerVersion ||
        query.error ||
        query.loading
      )
        return { accepted: false };
      const reply = await save({
        variables: {
          input: {
            ...target,
            ifMatchVersion: reviewed.clusterVersion,
            expectedProviderVersion: reviewed.providerVersion,
            computeMode: mode,
            declaration,
          },
        },
      });
      const envelope = reply.data?.updateClusterModelRuntime;
      if (!envelope?.ok) return { accepted: false, message: envelope?.errors?.[0]?.message };
      // A committed declaration remains accepted even when its follow-up read or
      // admission refresh fails. It never represents a model deployment.
      if (!current()) return { accepted: true };
      try {
        const reads = await Promise.allSettled([
          query.refetch(),
          Promise.resolve(refreshAdmission()),
        ]);
        if (reads.some((read) => read.status === "rejected"))
          return { accepted: true, refreshFailed: true };
        return { accepted: true };
      } catch {
        return { accepted: true, refreshFailed: true };
      }
    },
  };
}
