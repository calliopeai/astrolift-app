"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { PAUSE_ENVIRONMENT, RESUME_ENVIRONMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface Resp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

function reportResult(
  label: string,
  result: MutationResultLite<AstroliftAppEnvironment> | null | undefined
) {
  if (!result) return;
  if (result.ok) {
    toast.success(`${label}: ${result.data?.deploysPaused ? "paused" : "active"}`);
  } else {
    throw new Error(result.errors[0]?.message ?? `${label} failed`);
  }
}

/**
 * Environments across the org, or for one app when appSlug is set, plus
 * the pause / resume deploy mutations. Polls every 30s. The data half of
 * EnvironmentsScreen.
 */
export function useEnvironments(appSlug?: string) {
  const { can } = useMyPermissions();
  const router = useRouter();
  const variables = { appSlug: appSlug ?? null };

  const { data, loading } = useQuery<Resp>(LIST_ENVIRONMENTS, {
    variables,
    pollInterval: 30000,
  });

  const refetch = [{ query: LIST_ENVIRONMENTS, variables }];
  const [pause, pauseState] = useMutation<{
    pauseEnvironment: MutationResultLite<AstroliftAppEnvironment>;
  }>(PAUSE_ENVIRONMENT, { refetchQueries: refetch });
  const [resume, resumeState] = useMutation<{
    resumeEnvironment: MutationResultLite<AstroliftAppEnvironment>;
  }>(RESUME_ENVIRONMENT, { refetchQueries: refetch });

  /** Throws on failure so the confirm dialog shows the error and stays open. */
  async function onPause(e: AstroliftAppEnvironment) {
    const { data } = await pause({ variables: { input: { id: e.id } } });
    reportResult("pauseEnvironment", data?.pauseEnvironment);
  }

  async function onResume(e: AstroliftAppEnvironment) {
    const { data } = await resume({ variables: { input: { id: e.id } } });
    reportResult("resumeEnvironment", data?.resumeEnvironment);
  }

  function onOpen(e: AstroliftAppEnvironment) {
    router.push(`/environments/${e.id}`);
  }

  return {
    loading,
    environments: data?.astroliftEnvironments ?? [],
    busy: pauseState.loading || resumeState.loading,
    canPause: can("app.deploy"),
    onPause,
    onResume,
    onOpen,
  };
}

export type EnvironmentsState = ReturnType<typeof useEnvironments>;
