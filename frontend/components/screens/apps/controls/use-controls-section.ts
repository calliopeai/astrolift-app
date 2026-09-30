"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  useWorkloadActionPermission,
  workloadActionRefetch,
} from "../workloads/use-workload-action-permission";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import {
  PAUSE_ENVIRONMENT,
  RESTART_WORKLOAD,
  RESUME_ENVIRONMENT,
  SCALE_WORKLOAD,
  START_DEPLOYMENT,
  TRIGGER_DEPLOY_WORKFLOW,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface AstroliftTriggerDeployWorkflowPayload {
  runUrl: string;
  dispatchedBranch: string;
}

interface TriggerDeployResp {
  triggerAstroliftDeployWorkflow: MutationResult<AstroliftTriggerDeployWorkflowPayload>;
}

// #388 — live workload ops
interface WorkloadOpPayload {
  workloadId: string;
  newRevision: number | null;
  desiredReplicas: number | null;
  readyReplicas: number | null;
}

interface RestartWorkloadResp {
  restartAstroliftWorkload: MutationResult<WorkloadOpPayload>;
}

interface ScaleWorkloadResp {
  scaleAstroliftWorkload: MutationResult<WorkloadOpPayload>;
}

interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface StartResp {
  startDeployment: MutationResult<AstroliftDeployment>;
}

interface PauseResp {
  pauseEnvironment: MutationResult<AstroliftAppEnvironment>;
}

interface ResumeResp {
  resumeEnvironment: MutationResult<AstroliftAppEnvironment>;
}

/**
 * Environments + the app's workloads for the live operational controls
 * (#402). The data half of ControlsSectionView.
 *
 * Restart and scale target the primary environment, independently of env controls.
 */
export function useControlsSection(appSlug: string) {
  const { data, loading } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  const workloadsQuery = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const workloads = (workloadsQuery.data?.astroliftWorkloads ?? []).filter(
    // Deployment-kind workloads only — Jobs/CronJobs don't have
    // `spec.replicas` and a rolling-restart is meaningless for them.
    (w) => w.kind === "deployment"
  );
  const workloadsLoading = workloadsQuery.loading && workloads.length === 0;

  return { envs, loading: loading && envs.length === 0, workloads, workloadsLoading };
}

/**
 * One environment row's env-level toggles: pause/resume, deploy now,
 * rebuild & deploy. The data half of EnvironmentControlsView.
 */
export function useEnvironmentControls(
  env: AstroliftAppEnvironment,
  appSlug: string,
  deployBranch: string
) {
  const t = useTranslations("apps.settings.controls");
  const [pause, { loading: pausing }] = useMutation<PauseResp>(PAUSE_ENVIRONMENT, {
    refetchQueries: [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }],
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });
  const [resume, { loading: resuming }] = useMutation<ResumeResp>(RESUME_ENVIRONMENT, {
    refetchQueries: [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }],
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });
  const [deploy, { loading: deploying }] = useMutation<StartResp>(START_DEPLOYMENT, {
    refetchQueries: ["ListDeployments"],
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });
  const [rebuildAndDeploy, { loading: rebuilding }] = useMutation<TriggerDeployResp>(
    TRIGGER_DEPLOY_WORKFLOW,
    {
      // The dispatch doesn't create a Deployment row itself (CI does
      // that on its way through); the deployment list will refresh on
      // its own when the workflow lands a startDeployment. Refetching
      // here would be empty churn.
    }
  );
  const { data: deploymentsData } = useQuery<{ astroliftDeployments: AstroliftDeployment[] }>(
    LIST_DEPLOYMENTS,
    { variables: { appSlug, environmentName: env.name, limit: 1 }, fetchPolicy: "cache-first" }
  );
  const lastTag = deploymentsData?.astroliftDeployments?.[0]?.imageTag ?? "";

  async function onTogglePause() {
    try {
      const fn = env.deploysPaused ? resume : pause;
      const res = await fn({ variables: { input: { id: env.id } } });
      // The mutation result envelope differs by mutation name; both share
      // `ok` and `errors[]` so we don't need to discriminate further.
      const result = env.deploysPaused
        ? (res.data as ResumeResp | null | undefined)?.resumeEnvironment
        : (res.data as PauseResp | null | undefined)?.pauseEnvironment;
      if (result?.ok) {
        toast.success(
          env.deploysPaused
            ? t("toastDeploysResumed", { env: env.name })
            : t("toastDeploysPaused", { env: env.name })
        );
      } else {
        toast.error(result?.errors?.[0]?.message ?? t("toastActionFailed"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastActionFailed"));
    }
  }

  /** Resolves true when the deploy started, so the view can clear its tag input. */
  async function onDeploy(imageTag: string): Promise<boolean> {
    const tag = imageTag.trim() || lastTag || "latest";
    // A thrown mutation (network failure, GraphQL-level error outside the
    // envelope) previously escaped as an unhandled rejection — the click
    // produced no toast, no row, nothing. Silent failure is the one
    // outcome a deploy button must never have.
    let data: StartResp | null | undefined;
    try {
      ({ data } = await deploy({
        variables: {
          input: {
            appSlug,
            environmentName: env.name,
            imageTag: tag,
            triggerKind: "manual",
            branch: deployBranch || null,
          },
        },
      }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastDeployFailed"));
      return false;
    }
    if (data?.startDeployment.ok) {
      toast.success(t("toastDeployStarted", { env: env.name, tag }));
      return true;
    }
    toast.error(data?.startDeployment.errors?.[0]?.message ?? t("toastDeployFailed"));
    return false;
  }

  async function onRebuildAndDeploy() {
    // Same silent-failure guard as onDeploy.
    let data: TriggerDeployResp | null | undefined;
    try {
      ({ data } = await rebuildAndDeploy({
        variables: {
          input: {
            appSlug,
            branch: deployBranch || null,
          },
        },
      }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastRebuildFailed"));
      return;
    }
    const payload = data?.triggerAstroliftDeployWorkflow;
    if (payload?.ok && payload.data?.runUrl) {
      const runUrl = payload.data.runUrl;
      toast.success(t("toastCiDispatched"), {
        description: t("toastCiBranch", { branch: payload.data.dispatchedBranch }),
        action: {
          label: t("toastCiOpenRun"),
          onClick: () => window.open(runUrl, "_blank", "noopener,noreferrer"),
        },
      });
    } else if (payload?.ok) {
      // Defensive: ok=true without a runUrl is a backend contract
      // violation, but don't break the operator's session over it.
      toast.success(t("toastCiDispatched"));
    } else {
      toast.error(payload?.errors?.[0]?.message ?? t("toastRebuildFailed"));
    }
  }

  return {
    env,
    lastTag,
    pausing,
    resuming,
    deploying,
    rebuilding,
    onTogglePause,
    onDeploy,
    onRebuildAndDeploy,
  };
}

/**
 * One workload's rolling restart + replica scale in the primary environment. The
 * data half of WorkloadOpsRowView.
 */
export function useWorkloadOps(workload: AstroliftWorkload) {
  const t = useTranslations("apps.settings.controls");
  const actions = useTranslations("apps.workloadActions");
  const envName = actions("primary");
  const restartFeedback = useWorkloadActionPermission(
    workload.viewerCan?.restart,
    workload.version
  );
  const scaleFeedback = useWorkloadActionPermission(workload.viewerCan?.scale, workload.version);
  const [restart, { loading: restarting }] = useMutation<RestartWorkloadResp>(
    RESTART_WORKLOAD,
    workloadActionRefetch
  );
  const [scale, { loading: scaling }] = useMutation<ScaleWorkloadResp>(
    SCALE_WORKLOAD,
    workloadActionRefetch
  );

  async function onRestart() {
    if (restartFeedback.blocked()) return false;
    try {
      const { data } = await restart({
        variables: { input: { workloadId: workload.id }, ifMatchVersion: workload.version },
      });
      const payload = data?.restartAstroliftWorkload;
      if (payload?.ok) {
        toast.success(t("toastRestartIssued", { workload: workload.name, env: envName }));
        return true;
      } else {
        return restartFeedback.reject(payload, t("toastRestartFailed"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastRestartFailed"));
      return false;
    }
  }

  /** Resolves false on a rejected scale so the view can revert its staged counter. */
  async function onApply(replicas: number): Promise<boolean> {
    if (scaleFeedback.blocked()) return false;
    try {
      const { data } = await scale({
        variables: {
          input: { workloadId: workload.id, replicas },
          ifMatchVersion: workload.version,
        },
      });
      const payload = data?.scaleAstroliftWorkload;
      if (payload?.ok) {
        const desired = payload.data?.desiredReplicas ?? replicas;
        toast.success(t("toastScaled", { workload: workload.name, env: envName, count: desired }));
        return true;
      }
      return scaleFeedback.reject(payload, t("toastScaleFailed"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastScaleFailed"));
      return false;
    }
  }

  return {
    envName,
    workload,
    restarting,
    scaling,
    onRestart,
    onApply,
    restartPermission: restartFeedback.permission,
    scalePermission: scaleFeedback.permission,
  };
}
