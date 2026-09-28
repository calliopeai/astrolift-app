"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { RUN_JOB_ONCE } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

interface EnvsListResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface RunJobOnceResp {
  runAstroliftJobOnce: MutationResult<{
    runName: string;
    namespace: string;
    logsUrl: string | null;
  }>;
}

/**
 * "Run scheduled job once" (#390): the app's cronjob workloads and
 * environments, and the mutation that dispatches one ad-hoc k8s Job from a
 * workload's jobTemplate. The schedule is untouched. On success the toast
 * links to the scheduled-job-runs page.
 */
export function useRunScheduledJob(appSlug: string) {
  const t = useTranslations("apps.settings.runJob");
  const router = useRouter();
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = useQuery<EnvsListResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const [runJobOnce, { loading: running }] = useMutation<RunJobOnceResp>(RUN_JOB_ONCE);

  const cronJobs = (workloads.data?.astroliftWorkloads ?? []).filter((w) => w.kind === "cronjob");
  const environments = envs.data?.astroliftEnvironments ?? [];

  async function onRun(jobSlug: string, envName: string) {
    if (!jobSlug || !envName) return;
    try {
      const { data } = await runJobOnce({
        variables: {
          input: {
            appSlug,
            environmentName: envName,
            jobSlug,
          },
        },
      });
      const env = data?.runAstroliftJobOnce;
      if (!env) {
        toast.error("Run failed: no response from backend.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Run failed.");
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error("Run returned no payload.");
        return;
      }
      const message = `Started job ${payload.runName} in ${payload.namespace}.`;
      const logsUrl = payload.logsUrl;
      if (logsUrl) {
        toast.success(message, {
          duration: 8000,
          action: {
            label: t("viewRuns"),
            onClick: () => router.push(logsUrl),
          },
        });
      } else {
        toast.success(message);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Run failed.");
    }
  }

  return {
    appSlug,
    cronJobs,
    environments,
    /** First load of the workloads: the card stays hidden until it lands. */
    workloadsLoading: workloads.loading && !workloads.data,
    running,
    onRun,
  };
}
