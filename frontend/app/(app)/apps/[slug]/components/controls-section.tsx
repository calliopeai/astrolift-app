"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  HammerIcon,
  Loader2Icon,
  MinusIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  RocketIcon,
  RotateCwIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/Can";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
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

interface Props {
  appSlug: string;
  deployBranch: string;
}

/**
 * Live operational controls. Pause/resume is per-environment so the
 * operator can freeze prod during an incident without losing the ability
 * to ship to staging. Manual deploy reuses the latest known image tag for
 * the chosen env — when none exists, the trigger label still says "Deploy"
 * and the backend will reject with PRECONDITION_FAILED.
 */
export function ControlsSection({ appSlug, deployBranch }: Props) {
  const { data, loading } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-4">
        <h2 className="text-base font-semibold">Controls</h2>
        <p className="text-muted-foreground mt-0.5 text-xs">
          Live operational toggles. Take effect immediately — no CI roundtrip.
        </p>
      </div>

      {loading && envs.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">
          No environments yet — sync the manifest to populate this section.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {envs.map((env) => (
            <EnvironmentRow key={env.id} env={env} appSlug={appSlug} deployBranch={deployBranch} />
          ))}
        </div>
      )}

      <WorkloadOpsPanel appSlug={appSlug} />
    </section>
  );
}

/**
 * Per-workload incident-response controls (#388): rolling restart +
 * replica scale. Rendered below the per-env controls because these
 * operate on the running Deployment directly, independent of any
 * deploy state — appropriate when the operator wants to recycle pods
 * or react to a traffic spike without spinning up a fresh deploy.
 */
function WorkloadOpsPanel({ appSlug }: { appSlug: string }) {
  const { data, loading } = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  // Deployment-kind workloads only — Jobs/CronJobs don't have
  // `spec.replicas` and a rolling-restart is meaningless for them.
  const workloads = (data?.astroliftWorkloads ?? []).filter((w) => w.kind === "deployment");

  return (
    <div className="mt-5 border-t pt-4">
      <div className="mb-3">
        <h3 className="text-sm font-semibold">Workload ops</h3>
        <p className="text-muted-foreground mt-0.5 text-xs">
          Rolling restart and replica scale on the running Deployment. No image change.
        </p>
      </div>
      {loading && workloads.length === 0 ? (
        <div className="grid gap-2">
          <Skeleton className="h-16 w-full" />
        </div>
      ) : workloads.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">
          No Deployment workloads yet — sync the manifest to populate this section.
        </p>
      ) : (
        <div className="grid gap-2">
          {workloads.map((w) => (
            <WorkloadRow key={w.id} workload={w} appSlug={appSlug} />
          ))}
        </div>
      )}
    </div>
  );
}

function EnvironmentRow({
  env,
  appSlug,
  deployBranch,
}: {
  env: AstroliftAppEnvironment;
  appSlug: string;
  deployBranch: string;
}) {
  const [pause, { loading: pausing }] = useMutation<PauseResp>(PAUSE_ENVIRONMENT, {
    refetchQueries: [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }],
    awaitRefetchQueries: true,
  });
  const [resume, { loading: resuming }] = useMutation<ResumeResp>(RESUME_ENVIRONMENT, {
    refetchQueries: [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }],
    awaitRefetchQueries: true,
  });
  const [deploy, { loading: deploying }] = useMutation<StartResp>(START_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { appSlug, limit: 10 } }],
  });
  const [rebuildAndDeploy, { loading: rebuilding }] = useMutation<TriggerDeployResp>(
    TRIGGER_DEPLOY_WORKFLOW,
    {
      // The dispatch doesn't create a Deployment row itself (CI does
      // that on its way through); the deployment list will refresh on
      // its own when the workflow lands a startDeployment. Refetching
      // here would be empty churn.
    },
  );
  const [imageTag, setImageTag] = useState("");

  async function handleTogglePause() {
    const fn = env.deploysPaused ? resume : pause;
    const res = await fn({ variables: { input: { id: env.id } } });
    // The mutation result envelope differs by mutation name; both share
    // `ok` and `errors[]` so we don't need to discriminate further.
    const result = env.deploysPaused
      ? (res.data as ResumeResp | null | undefined)?.resumeEnvironment
      : (res.data as PauseResp | null | undefined)?.pauseEnvironment;
    if (result?.ok) {
      toast.success(env.deploysPaused ? "Deploys resumed." : "Deploys paused.");
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Action failed.");
    }
  }

  async function handleDeploy() {
    const tag = imageTag.trim() || "latest";
    const { data } = await deploy({
      variables: {
        input: {
          appSlug,
          environmentName: env.name,
          imageTag: tag,
          triggerKind: "manual",
          branch: deployBranch || null,
        },
      },
    });
    if (data?.startDeployment.ok) {
      toast.success(`Deploy started for ${env.name} · ${tag}.`);
      setImageTag("");
    } else {
      toast.error(data?.startDeployment.errors?.[0]?.message ?? "Deploy failed.");
    }
  }

  async function handleRebuildAndDeploy() {
    const { data } = await rebuildAndDeploy({
      variables: {
        input: {
          appSlug,
          branch: deployBranch || null,
        },
      },
    });
    const payload = data?.triggerAstroliftDeployWorkflow;
    if (payload?.ok && payload.data?.runUrl) {
      const runUrl = payload.data.runUrl;
      toast.success("CI workflow dispatched.", {
        description: `Branch: ${payload.data.dispatchedBranch}`,
        action: {
          label: "Open run →",
          onClick: () => window.open(runUrl, "_blank", "noopener,noreferrer"),
        },
      });
    } else if (payload?.ok) {
      // Defensive: ok=true without a runUrl is a backend contract
      // violation, but don't break the operator's session over it.
      toast.success("CI workflow dispatched.");
    } else {
      toast.error(payload?.errors?.[0]?.message ?? "Rebuild & deploy failed.");
    }
  }

  return (
    <div className="bg-card flex flex-col gap-3 rounded-md border p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium capitalize">{env.name}</span>
          {env.deploysPaused ? (
            <Badge
              variant="outline"
              className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
            >
              <PauseIcon className="size-3" />
              Paused
            </Badge>
          ) : (
            <Badge variant="outline" className="text-muted-foreground">
              <PlayIcon className="size-3" />
              Active
            </Badge>
          )}
        </div>
        <Can permission="app.deploy">
          <Button
            size="sm"
            variant={env.deploysPaused ? "default" : "outline"}
            onClick={handleTogglePause}
            disabled={pausing || resuming}
          >
            {pausing || resuming ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : env.deploysPaused ? (
              <PlayIcon className="size-3.5" />
            ) : (
              <PauseIcon className="size-3.5" />
            )}
            {env.deploysPaused ? "Resume" : "Pause"}
          </Button>
        </Can>
      </div>

      <Can permission="app.deploy">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={imageTag}
            onChange={(e) => setImageTag(e.target.value)}
            placeholder="image tag (latest)"
            className="border-input bg-background flex-1 rounded-md border px-2 py-1 font-mono text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={handleDeploy}
            disabled={deploying || env.deploysPaused}
            title={
              env.deploysPaused
                ? "Resume deploys first."
                : "Deploys an existing image tag. Use 'Rebuild & deploy' to rebuild from source."
            }
            className="gap-1.5"
          >
            {deploying ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RocketIcon className="size-3.5" />
            )}
            Deploy now
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={handleRebuildAndDeploy}
            disabled={rebuilding || env.deploysPaused}
            title={
              env.deploysPaused
                ? "Resume deploys first."
                : "Rebuilds the image from main and runs the deploy end-to-end. Use 'Deploy now' if you want to redeploy an existing image tag."
            }
            className="gap-1.5"
          >
            {rebuilding ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            Rebuild &amp; deploy
          </Button>
        </div>
      </Can>

      {env.requiredApprovals > 0 && (
        <p className="text-muted-foreground flex items-center gap-1.5 text-[11px]">
          <HammerIcon className="size-3" />
          Requires {env.requiredApprovals} approval{env.requiredApprovals === 1 ? "" : "s"}
          before rollout
        </p>
      )}
    </div>
  );
}

/**
 * One row in the workload-ops panel: a rolling-restart button and an
 * adjustable replica counter (− / N / + / Apply) per workload. The
 * counter is locally controlled so the operator can stage a value
 * before applying it; Apply is what actually fires the scale mutation.
 * On success we refetch the workloads query so the displayed ready/
 * desired counts converge.
 *
 * The platform clamps replicas server-side (0 ≤ replicas ≤ min(20,
 * env.max_replicas)); the FE doesn't shadow that policy because env
 * overrides aren't surfaced through the workload row today — the
 * server returns the bounds in the error message when an out-of-range
 * Apply lands.
 */
function WorkloadRow({
  workload,
  appSlug,
}: {
  workload: AstroliftWorkload;
  appSlug: string;
}) {
  const [restart, { loading: restarting }] = useMutation<RestartWorkloadResp>(
    RESTART_WORKLOAD,
    {
      refetchQueries: [{ query: LIST_WORKLOADS, variables: { appSlug } }],
      awaitRefetchQueries: true,
    },
  );
  const [scale, { loading: scaling }] = useMutation<ScaleWorkloadResp>(SCALE_WORKLOAD, {
    refetchQueries: [{ query: LIST_WORKLOADS, variables: { appSlug } }],
    awaitRefetchQueries: true,
  });

  // The counter is staged locally so adjustments feel snappy and the
  // operator can back out before committing. We re-sync from the
  // server when `workload.replicas` shifts (e.g. another tab applies).
  const initial = Math.max(0, workload.replicas ?? 0);
  const [pending, setPending] = useState<number>(initial);
  useEffect(() => {
    setPending(Math.max(0, workload.replicas ?? 0));
  }, [workload.replicas]);

  const dirty = pending !== initial;
  // V1 hard ceiling — env overrides only tighten this; the server
  // is authoritative on validation.
  const HARD_UPPER = 20;
  const HARD_LOWER = 0;

  async function handleRestart() {
    if (
      !window.confirm(
        `Restart all replicas of ${workload.name}? Pods roll one-by-one — long-running connections drain.`,
      )
    ) {
      return;
    }
    const { data } = await restart({ variables: { input: { workloadId: workload.id } } });
    const payload = data?.restartAstroliftWorkload;
    if (payload?.ok) {
      toast.success(`Rolling restart issued for ${workload.name}.`);
    } else {
      toast.error(payload?.errors?.[0]?.message ?? "Rolling restart failed.");
    }
  }

  async function handleApply() {
    const { data } = await scale({
      variables: { input: { workloadId: workload.id, replicas: pending } },
    });
    const payload = data?.scaleAstroliftWorkload;
    if (payload?.ok) {
      const desired = payload.data?.desiredReplicas ?? pending;
      toast.success(`${workload.name} scaled to ${desired} replica${desired === 1 ? "" : "s"}.`);
    } else {
      toast.error(payload?.errors?.[0]?.message ?? "Scale failed.");
      // Revert local pending so the displayed counter matches reality
      // on validation failure.
      setPending(initial);
    }
  }

  return (
    <div className="bg-card flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium">{workload.name}</span>
        <Badge variant="outline" className="text-muted-foreground font-mono text-[10px]">
          {workload.slug}
        </Badge>
        <span className="text-muted-foreground text-xs">{initial}/{initial} ready</span>
      </div>
      <Can permission="app.deploy">
        <div className="flex items-center gap-2">
          <div className="flex items-center rounded-md border">
            <Button
              size="sm"
              variant="ghost"
              className="size-7 rounded-none p-0"
              onClick={() => setPending((v) => Math.max(HARD_LOWER, v - 1))}
              disabled={scaling || pending <= HARD_LOWER}
              aria-label="Decrease replicas"
            >
              <MinusIcon className="size-3.5" />
            </Button>
            <span className="border-x px-3 text-center font-mono text-xs tabular-nums">
              {pending}
            </span>
            <Button
              size="sm"
              variant="ghost"
              className="size-7 rounded-none p-0"
              onClick={() => setPending((v) => Math.min(HARD_UPPER, v + 1))}
              disabled={scaling || pending >= HARD_UPPER}
              aria-label="Increase replicas"
            >
              <PlusIcon className="size-3.5" />
            </Button>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={handleApply}
            disabled={scaling || !dirty}
            className="gap-1.5"
          >
            {scaling ? <Loader2Icon className="size-3.5 animate-spin" /> : null}
            Apply
          </Button>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                onClick={handleRestart}
                disabled={restarting}
                className="gap-1.5"
              >
                {restarting ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RotateCwIcon className="size-3.5" />
                )}
                Rolling restart
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              Recycle pods without changing the image — drains long-running connections,
              propagates sidecar updates.
            </TooltipContent>
          </Tooltip>
        </div>
      </Can>
    </div>
  );
}
