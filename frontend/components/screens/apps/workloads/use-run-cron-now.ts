"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { RUN_JOB_ONCE } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

/**
 * Run a CronJob now, from its row on the Workloads tab: the app's
 * environments to run it in, and `runAstroliftJobOnce`, as the scheduled
 * jobs view did before jobs folded into the tab as a kind. The resolver
 * refuses a viewer without app.deploy, so the menu hides it for them.
 */
export function useRunCronNow(appSlug: string) {
  const { can } = useMyPermissions();
  const envs = useQuery<{ astroliftEnvironments: AstroliftAppEnvironment[] }>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const [pendingSlug, setPendingSlug] = React.useState<string | null>(null);

  const [runOnce] = useMutation<{
    runAstroliftJobOnce: {
      ok: boolean;
      errors: { code: string; message: string }[];
      data: { runName: string; namespace: string; logsUrl: string } | null;
    };
  }>(RUN_JOB_ONCE, {
    // By operation name: the run lists carry cursors and filters.
    refetchQueries: ["ListCronJobsPage", "ListScheduledJobRunsPage"],
  });

  async function onRun(jobSlug: string, environmentName: string) {
    setPendingSlug(jobSlug);
    try {
      const { data } = await runOnce({
        variables: { input: { appSlug, environmentName, jobSlug } },
      });
      const res = data?.runAstroliftJobOnce;
      if (res?.ok) {
        toast.success(
          `${jobSlug} dispatched as ${res.data?.runName ?? "manual run"} (${environmentName})`
        );
      } else {
        toast.error(res?.errors?.[0]?.message ?? "Run failed");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Run failed");
    } finally {
      setPendingSlug(null);
    }
  }

  return {
    environments: (envs.data?.astroliftEnvironments ?? []).map((e) => ({ id: e.id, name: e.name })),
    pendingSlug,
    canRun: can("app.deploy"),
    onRun: (jobSlug: string, environmentName: string) => void onRun(jobSlug, environmentName),
  };
}
