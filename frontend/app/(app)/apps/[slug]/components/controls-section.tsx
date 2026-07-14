"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  HammerIcon,
  Loader2Icon,
  MinusIcon,
  MoreHorizontalIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  RocketIcon,
  RotateCwIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/Can";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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
 * Live operational controls (#402). One row per environment; each row
 * carries the env-level toggles (pause/resume, deploy now, rebuild &
 * deploy) AND the inline workload-ops list (rolling restart + replica
 * scale) for that env. The standalone workload-ops panel that used to
 * sit below the env grid is gone — operators triaging the prod row
 * no longer have to scroll past dev/stg to reach its workloads.
 *
 * Workloads aren't env-scoped in the schema today (LIST_WORKLOADS is
 * app-scoped; the same Deployment definition fans out across envs), so
 * each env card renders the same set of workload rows. The actions
 * themselves are scoped per-env in the confirmation copy — a rolling
 * restart in the prod row names "prod" explicitly so the operator can't
 * misread which env they're touching. If/when the backend grows a
 * per-env workload query the data plane catches up without a UX shift.
 */
export function ControlsSection({ appSlug, deployBranch }: Props) {
  const t = useTranslations("apps.settings.controls");
  const { data, loading } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  // The workload list is shared across env rows (one query, one source
  // of truth). Each env card reads from this collection so we don't
  // multiply the network call by env count. Refetches after a per-env
  // workload op converge every row at once.
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

  // Rendered inside the page-level "Controls" Section — h3 keeps the
  // document outline nested instead of emitting a sibling h2.
  return (
    <Section headingLevel="h3" title={t("title")} description={t("description")}>
      {loading && envs.length === 0 ? (
        <div className="grid gap-3">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{t("emptyEnvs")}</p>
      ) : (
        <div className="grid gap-3">
          {envs.map((env) => (
            <EnvironmentRow
              key={env.id}
              env={env}
              appSlug={appSlug}
              deployBranch={deployBranch}
              workloads={workloads}
              workloadsLoading={workloadsLoading}
            />
          ))}
        </div>
      )}
    </Section>
  );
}

function EnvironmentRow({
  env,
  appSlug,
  deployBranch,
  workloads,
  workloadsLoading,
}: {
  env: AstroliftAppEnvironment;
  appSlug: string;
  deployBranch: string;
  workloads: AstroliftWorkload[];
  workloadsLoading: boolean;
}) {
  const t = useTranslations("apps.settings.controls");
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
    }
  );
  const { data: deploymentsData } = useQuery<{ astroliftDeployments: AstroliftDeployment[] }>(
    LIST_DEPLOYMENTS,
    { variables: { appSlug, environmentName: env.name, limit: 1 }, fetchPolicy: "cache-first" }
  );
  const lastTag = deploymentsData?.astroliftDeployments?.[0]?.imageTag ?? "";
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
      toast.success(
        env.deploysPaused
          ? t("toastDeploysResumed", { env: env.name })
          : t("toastDeploysPaused", { env: env.name })
      );
    } else {
      toast.error(result?.errors?.[0]?.message ?? t("toastActionFailed"));
    }
  }

  async function handleDeploy() {
    const tag = imageTag.trim() || lastTag || "latest";
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
      toast.success(t("toastDeployStarted", { env: env.name, tag }));
      setImageTag("");
    } else {
      toast.error(data?.startDeployment.errors?.[0]?.message ?? t("toastDeployFailed"));
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

  return (
    <Card className="px-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium capitalize">{env.name}</span>
          {env.deploysPaused ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              <PauseIcon className="size-3" />
              {t("paused")}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-muted-foreground">
              <PlayIcon className="size-3" />
              {t("active")}
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
            {env.deploysPaused ? t("resume") : t("pause")}
          </Button>
        </Can>
      </div>

      <Can permission="app.deploy">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <input
            type="text"
            value={imageTag}
            onChange={(e) => setImageTag(e.target.value)}
            placeholder={lastTag ? lastTag : t("imageTagPlaceholder")}
            className="border-input bg-background flex-1 rounded-md border px-2 py-1 font-mono text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={handleDeploy}
            disabled={deploying || env.deploysPaused}
            title={env.deploysPaused ? t("tooltipResumeFirst") : t("tooltipDeployNow")}
            className="gap-1.5"
          >
            {deploying ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RocketIcon className="size-3.5" />
            )}
            {t("deployNow")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleRebuildAndDeploy}
            disabled={rebuilding || env.deploysPaused}
            title={env.deploysPaused ? t("tooltipResumeFirst") : t("tooltipRebuildAndDeploy")}
            className="gap-1.5"
          >
            {rebuilding ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            {t("rebuildAndDeploy")}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">{t("deployCaption")}</p>
      </Can>

      {env.requiredApprovals > 0 && (
        <p className="text-muted-foreground text-2xs flex items-center gap-1.5">
          <HammerIcon className="size-3" />
          {t("approvalsRequired", { count: env.requiredApprovals })}
        </p>
      )}

      <EnvWorkloads
        envName={env.name}
        appSlug={appSlug}
        workloads={workloads}
        loading={workloadsLoading}
      />
    </Card>
  );
}

/**
 * Per-env workload-ops list (#402). Renders the rolling-restart +
 * replica scale rows nested inside an EnvironmentRow. Workload list
 * is shared across env rows today (see ControlsSection note); the
 * env-scoped confirmation copy still gives the operator unambiguous
 * intent at the action moment.
 */
function EnvWorkloads({
  envName,
  appSlug,
  workloads,
  loading,
}: {
  envName: string;
  appSlug: string;
  workloads: AstroliftWorkload[];
  loading: boolean;
}) {
  const t = useTranslations("apps.settings.controls");
  return (
    <div className="border-t pt-3">
      <p className="text-muted-foreground text-2xs mb-2 font-semibold tracking-wide uppercase">
        {t("workloadsHeader")}
      </p>
      {loading ? (
        <Skeleton className="h-14 w-full" />
      ) : workloads.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{t("workloadsEmpty")}</p>
      ) : (
        <div className="grid gap-2">
          {workloads.map((w) => (
            <WorkloadRow
              key={`${envName}-${w.id}`}
              envName={envName}
              workload={w}
              appSlug={appSlug}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One row in the per-env workload-ops list: a rolling-restart button
 * and an adjustable replica counter (− / N / + / Apply) per workload.
 * The counter is locally controlled so the operator can stage a value
 * before applying it; Apply is what actually fires the scale mutation.
 *
 * On `< sm` viewports the action cluster collapses into a kebab
 * `DropdownMenu` (pattern from #412 `clusters-client.tsx`) — same
 * actions, half the row height. The counter stays inline on desktop;
 * mobile users open the dropdown to access decrease/increase/apply.
 *
 * The platform clamps replicas server-side (0 ≤ replicas ≤ min(20,
 * env.max_replicas)); the FE doesn't shadow that policy because env
 * overrides aren't surfaced through the workload row today — the
 * server returns the bounds in the error message when an out-of-range
 * Apply lands.
 */
function WorkloadRow({
  envName,
  workload,
  appSlug,
}: {
  envName: string;
  workload: AstroliftWorkload;
  appSlug: string;
}) {
  const t = useTranslations("apps.settings.controls");
  const [restart, { loading: restarting }] = useMutation<RestartWorkloadResp>(RESTART_WORKLOAD, {
    refetchQueries: [{ query: LIST_WORKLOADS, variables: { appSlug } }],
    awaitRefetchQueries: true,
  });
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
  const [restartConfirmOpen, setRestartConfirmOpen] = useState(false);
  // V1 hard ceiling — env overrides only tighten this; the server
  // is authoritative on validation.
  const HARD_UPPER = 20;
  const HARD_LOWER = 0;

  async function handleRestart() {
    const { data } = await restart({ variables: { input: { workloadId: workload.id } } });
    const payload = data?.restartAstroliftWorkload;
    if (payload?.ok) {
      toast.success(t("toastRestartIssued", { workload: workload.name, env: envName }));
    } else {
      toast.error(payload?.errors?.[0]?.message ?? t("toastRestartFailed"));
    }
    setRestartConfirmOpen(false);
  }

  async function handleApply() {
    const { data } = await scale({
      variables: { input: { workloadId: workload.id, replicas: pending } },
    });
    const payload = data?.scaleAstroliftWorkload;
    if (payload?.ok) {
      const desired = payload.data?.desiredReplicas ?? pending;
      toast.success(t("toastScaled", { workload: workload.name, env: envName, count: desired }));
    } else {
      toast.error(payload?.errors?.[0]?.message ?? t("toastScaleFailed"));
      // Revert local pending so the displayed counter matches reality
      // on validation failure.
      setPending(initial);
    }
  }

  const busy = scaling || restarting;

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium">{workload.name}</span>
        <Badge variant="outline" className="text-muted-foreground text-2xs font-mono">
          {workload.slug}
        </Badge>
        <span className="text-muted-foreground text-xs">
          {t("readyCount", { ready: initial, desired: initial })}
        </span>
      </div>
      <Can permission="app.deploy">
        {/* Desktop: inline counter + Apply + Rolling restart. */}
        <div className="hidden items-center gap-2 sm:flex">
          <div className="flex items-center rounded-md border">
            <Button
              size="sm"
              variant="ghost"
              className="size-7 rounded-none p-0"
              onClick={() => setPending((v) => Math.max(HARD_LOWER, v - 1))}
              disabled={scaling || pending <= HARD_LOWER}
              aria-label={t("ariaDecrease")}
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
              aria-label={t("ariaIncrease")}
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
            {t("apply")}
          </Button>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setRestartConfirmOpen(true)}
                disabled={restarting}
                className="gap-1.5"
              >
                {restarting ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RotateCwIcon className="size-3.5" />
                )}
                {t("rollingRestart")}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{t("rollingRestartTooltip")}</TooltipContent>
          </Tooltip>
        </div>
        <ConfirmDialog
          open={restartConfirmOpen}
          onOpenChange={setRestartConfirmOpen}
          title={t("confirmRestart", { workload: workload.name, env: envName })}
          description=""
          confirmLabel={t("rollingRestart")}
          onConfirm={handleRestart}
        />

        {/* Mobile: kebab dropdown carrying the same actions. Pattern
            mirrors clusters-client.tsx renderRowMenu from #412 — keeps
            env rows compact when stacked single-column. */}
        <div className="flex items-center justify-end gap-2 sm:hidden">
          <span className="border-input rounded-md border px-2 py-1 font-mono text-xs tabular-nums">
            {pending}
          </span>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-9"
                disabled={busy}
                aria-label={t("ariaWorkloadActions", { workload: workload.name })}
              >
                {busy ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <MoreHorizontalIcon className="size-4" />
                )}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel className="text-2xs font-normal">
                {t("dropdownLabel", { workload: workload.name, env: envName })}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  setPending((v) => Math.max(HARD_LOWER, v - 1));
                }}
                disabled={scaling || pending <= HARD_LOWER}
              >
                <MinusIcon className="size-4" />
                {t("decreaseReplica")}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  setPending((v) => Math.min(HARD_UPPER, v + 1));
                }}
                disabled={scaling || pending >= HARD_UPPER}
              >
                <PlusIcon className="size-4" />
                {t("increaseReplica")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void handleApply()} disabled={scaling || !dirty}>
                {scaling ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <RocketIcon className="size-4" />
                )}
                {t("applyScale", { count: pending })}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setRestartConfirmOpen(true)} disabled={restarting}>
                {restarting ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <RotateCwIcon className="size-4" />
                )}
                {t("rollingRestart")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </Can>
    </div>
  );
}
