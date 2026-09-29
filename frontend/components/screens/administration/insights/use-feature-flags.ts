"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type {
  AdminFeatureInventoryQuery,
  SetFeatureFlagMutation,
  SetFeatureFlagMutationVariables,
} from "@/graphql/__generated__/operations";
import { SET_FEATURE_FLAG } from "@/graphql/server/server.mutations";
import { ADMIN_FEATURE_INVENTORY, SERVER_INFO } from "@/graphql/server/server.queries";

import { humanizeKey } from "./feature-key";

export type RuntimeFlag = AdminFeatureInventoryQuery["astroliftServerInfo"]["featureFlags"][number];
export type BuildTimeFeature =
  AdminFeatureInventoryQuery["astroliftServerInfo"]["buildTimeFeatures"][number];

/** The install's runtime flags and install-time features, and the flag toggle. */
export function useFeatureFlags() {
  const { data, loading, error, refetch } = useQuery<AdminFeatureInventoryQuery>(
    ADMIN_FEATURE_INVENTORY,
    {
      fetchPolicy: "cache-and-network",
    }
  );
  const [setFlag] = useMutation<SetFeatureFlagMutation, SetFeatureFlagMutationVariables>(
    SET_FEATURE_FLAG
  );
  const [pendingKey, setPendingKey] = React.useState<string | null>(null);

  // Throws on failure so ConfirmDialog keeps the dialog open and toasts the
  // reason, rather than closing on a flag that never moved.
  async function toggleFlag(flag: RuntimeFlag) {
    const next = !flag.enabled;
    setPendingKey(flag.key);
    try {
      const res = await setFlag({
        variables: { key: flag.key, enabled: next },
        // Refetch the screen AND the hot-path nav gate (SERVER_INFO) so a
        // flag that hides/shows a surface (e.g. zentinelle.enabled) updates
        // the sidebar without a reload.
        refetchQueries: [{ query: ADMIN_FEATURE_INVENTORY }, { query: SERVER_INFO }],
        awaitRefetchQueries: true,
      });
      const result = res.data?.setFeatureFlag;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Could not update the feature flag.");
      }
      toast.success(`${humanizeKey(flag.key)} ${next ? "enabled" : "disabled"}`);
    } finally {
      setPendingKey(null);
    }
  }

  return {
    runtimeFlags: data?.astroliftServerInfo?.featureFlags ?? [],
    buildTimeFeatures: data?.astroliftServerInfo?.buildTimeFeatures ?? [],
    loading,
    error: error ?? null,
    onRetry: (): void => {
      void refetch();
    },
    pendingKey,
    toggleFlag,
  };
}
