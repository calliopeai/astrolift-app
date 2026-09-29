"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { PAUSE_APP_INGRESS, RESUME_APP_INGRESS } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}
interface PauseIngressResp {
  pauseAppIngress: MutationResult<AstroliftAppEnvironment>;
}
interface ResumeIngressResp {
  resumeAppIngress: MutationResult<AstroliftAppEnvironment>;
}

/**
 * Per-environment ingress pause/resume, independent of the deploy-pause
 * toggle in ControlsSection. Pausing ingress takes the env offline at the
 * edge without stopping the workloads. `busyIds` holds the environments
 * with a toggle in flight, so each row spins on its own.
 */
export function useIngressControls(appSlug: string) {
  const { data, loading } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  const refetch = [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }];
  const [pause] = useMutation<PauseIngressResp>(PAUSE_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [resume] = useMutation<ResumeIngressResp>(RESUME_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [busyIds, setBusyIds] = React.useState<string[]>([]);

  async function onToggle(env: AstroliftAppEnvironment) {
    setBusyIds((ids) => [...ids, env.id]);
    try {
      if (env.ingressPaused) {
        const { data } = await resume({ variables: { input: { id: env.id } } });
        if (data?.resumeAppIngress.ok) {
          toast.success(`Ingress resumed for ${env.name}.`);
        } else {
          toast.error(data?.resumeAppIngress.errors?.[0]?.message ?? "Resume failed.");
        }
      } else {
        const { data } = await pause({ variables: { input: { id: env.id } } });
        if (data?.pauseAppIngress.ok) {
          toast.success(`Ingress paused for ${env.name}.`);
        } else {
          toast.error(data?.pauseAppIngress.errors?.[0]?.message ?? "Pause failed.");
        }
      }
    } finally {
      setBusyIds((ids) => ids.filter((id) => id !== env.id));
    }
  }

  return { envs, loading, busyIds, onToggle };
}
