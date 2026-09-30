"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import { useListState, useLocalListState } from "@/components/list/use-list-state";
import { PAUSE_ENVIRONMENT, RESUME_ENVIRONMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { localizedEnvironmentsList, environmentsVariables } from "./environments-list";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface Resp {
  astroliftEnvironmentsPage: { items: AstroliftAppEnvironment[]; totalCount: number | null };
}

/**
 * Environments across the org (URL list state), or one app's when appSlug
 * is set (in-memory list state: the host tab's own query must survive a
 * filter change): one numbered page of `astroliftEnvironmentsPage`, the
 * server filtering, sorting and counting, plus pause / resume deploys.
 * Polls every 30s. The data half of EnvironmentsScreen.
 */
export function useEnvironments(appSlug?: string) {
  const t = useTranslations("lists.environments");
  const definition = useMemo(() => localizedEnvironmentsList(t), [t]);
  const routed = useListState(definition);
  const local = useLocalListState(definition);
  const list = appSlug ? local : routed;
  const { state } = list;
  const { can } = useMyPermissions();
  const variables = environmentsVariables(appSlug ?? null, {
    q: state.q,
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const query = useQuery<Resp>(LIST_ENVIRONMENTS_PAGE, {
    variables,
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });
  const data = query.data ?? query.previousData;
  const rows = data?.astroliftEnvironmentsPage.items ?? [];
  const totalCount = data?.astroliftEnvironmentsPage.totalCount ?? rows.length;

  const refetch = [{ query: LIST_ENVIRONMENTS_PAGE, variables }];
  const [pause, pauseState] = useMutation<{
    pauseEnvironment: MutationResultLite<AstroliftAppEnvironment>;
  }>(PAUSE_ENVIRONMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: refetch });
  const [resume, resumeState] = useMutation<{
    resumeEnvironment: MutationResultLite<AstroliftAppEnvironment>;
  }>(RESUME_ENVIRONMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: refetch });

  function reportResult(
    action: string,
    result: MutationResultLite<AstroliftAppEnvironment> | null | undefined
  ) {
    if (!result) throw new Error(t("operationFailed", { action }));
    if (result.ok) {
      toast.success(
        t("operationResult", {
          action,
          status: t(result.data?.deploysPaused ? "paused" : "active"),
        })
      );
    } else {
      throw new Error(result.errors[0]?.message ?? t("operationFailed", { action }));
    }
  }

  /** Throws on failure so the confirm dialog shows the error and stays open. */
  async function onPause(e: AstroliftAppEnvironment) {
    const { data } = await pause({ variables: { input: { id: e.id } } });
    reportResult(t("pause"), data?.pauseEnvironment);
  }

  /** No confirm stands in front of resume, so a failure is a toast. */
  async function onResume(e: AstroliftAppEnvironment) {
    try {
      const { data } = await resume({ variables: { input: { id: e.id } } });
      reportResult(t("resume"), data?.resumeEnvironment);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("operationFailed", { action: t("resume") })
      );
    }
  }

  return {
    list,
    appSlug: appSlug ?? null,
    rows,
    totalCount,
    loading: query.loading && !data,
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
    busy: pauseState.loading || resumeState.loading,
    canPause: can("app.deploy"),
    onPause,
    onResume,
  };
}

export type EnvironmentsState = ReturnType<typeof useEnvironments>;
