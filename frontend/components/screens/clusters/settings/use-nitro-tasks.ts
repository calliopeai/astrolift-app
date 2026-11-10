"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { CONFIGURE_NITRO_TASKS, GET_NITRO_TASK_READINESS } from "@/graphql/clusters/nitro.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";

import type { NitroTaskReadiness, NitroTasksProps } from "./NitroTasks";

export const useNitroTasks = (clusterId: string): NitroTasksProps => {
  const t = useTranslations("clusterSettings.nitro");
  const { data, loading, error, refetch } = useQuery<{
    astroliftNitroTaskReadiness: MutationResult<NitroTaskReadiness>;
  }>(GET_NITRO_TASK_READINESS, {
    variables: { clusterId },
    fetchPolicy: "network-only",
    notifyOnNetworkStatusChange: true,
  });
  const [configure, { loading: saving }] = useMutation<{
    configureNitroTasks: MutationResult<NitroTaskReadiness>;
  }>(CONFIGURE_NITRO_TASKS, { fetchPolicy: "no-cache" });
  const result = data?.astroliftNitroTaskReadiness;
  const readiness =
    result?.ok && result.data?.clusterId === clusterId && !error ? result.data : null;
  return {
    readiness,
    loading,
    saving,
    error:
      error?.message ??
      (result && !result.ok ? (result.errors?.[0]?.message ?? t("failed")) : null),
    onRefresh: () => {
      void refetch().catch(() => undefined);
    },
    onChange: async (taskModes) => {
      if (!readiness || loading || saving) return false;
      try {
        const response = await configure({
          variables: {
            input: {
              clusterId,
              expectedVersion: readiness.version,
              expectedSource: readiness.sourceToken,
              taskModes,
            },
          },
        });
        if (!response.data?.configureNitroTasks.ok) {
          toast.error(response.data?.configureNitroTasks.errors?.[0]?.message ?? t("saveFailed"));
          await refetch();
          return false;
        }
        toast.success(t("saved"));
        await refetch();
        return true;
      } catch (failure) {
        toast.error(failure instanceof Error ? failure.message : t("saveFailed"));
        return false;
      }
    },
  };
};
