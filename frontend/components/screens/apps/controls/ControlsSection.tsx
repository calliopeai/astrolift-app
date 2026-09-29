"use client";

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
import * as React from "react";
import { useEffect, useState } from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type {
  useControlsSection,
  useEnvironmentControls,
  useWorkloadOps,
} from "./use-controls-section";

export type ControlsSectionViewProps = Pick<
  ReturnType<typeof useControlsSection>,
  "envs" | "loading"
> & {
  /** One EnvironmentControlsView per env; the route wires its hook. */
  renderEnvironment: (env: AstroliftAppEnvironment) => React.ReactNode;
};

/**
 * Live operational controls (#402). One row per environment; each row
 * carries the env-level toggles (pause/resume, deploy now, rebuild &
 * deploy) AND the inline workload-ops list (rolling restart + replica
 * scale) for that env. The standalone workload-ops panel that used to
 * sit below the env grid is gone — operators triaging the prod row
 * no longer have to scroll past dev/stg to reach its workloads.
 */
export function ControlsSectionView({
  envs,
  loading,
  renderEnvironment,
}: ControlsSectionViewProps) {
  const t = useTranslations("apps.settings.controls");

  // Rendered inside the page-level "Controls" Section — h3 keeps the
  // document outline nested instead of emitting a sibling h2.
  return (
    <Section headingLevel="h3" title={t("title")} description={t("description")}>
      {loading ? (
        <div className="grid gap-3">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{t("emptyEnvs")}</p>
      ) : (
        <div className="grid gap-3">
          {envs.map((env) => (
            <React.Fragment key={env.id}>{renderEnvironment(env)}</React.Fragment>
          ))}
        </div>
      )}
    </Section>
  );
}

export type EnvironmentControlsViewProps = ReturnType<typeof useEnvironmentControls> & {
  workloads: AstroliftWorkload[];
  workloadsLoading: boolean;
  /** One WorkloadOpsRowView per workload; the route wires its hook. */
  renderWorkload: (workload: AstroliftWorkload) => React.ReactNode;
};

export function EnvironmentControlsView({
  env,
  lastTag,
  pausing,
  resuming,
  deploying,
  rebuilding,
  onTogglePause,
  onDeploy,
  onRebuildAndDeploy,
  workloads,
  workloadsLoading,
  renderWorkload,
}: EnvironmentControlsViewProps) {
  const t = useTranslations("apps.settings.controls");
  const [imageTag, setImageTag] = useState("");

  async function handleDeploy() {
    if (await onDeploy(imageTag)) setImageTag("");
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
            onClick={onTogglePause}
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
            onClick={onRebuildAndDeploy}
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
        workloads={workloads}
        loading={workloadsLoading}
        renderWorkload={renderWorkload}
      />
    </Card>
  );
}

/**
 * Per-env workload-ops list (#402). Renders the rolling-restart +
 * replica scale rows nested inside an environment row. Workload list
 * is shared across env rows today (see useControlsSection); the
 * env-scoped confirmation copy still gives the operator unambiguous
 * intent at the action moment.
 */
function EnvWorkloads({
  envName,
  workloads,
  loading,
  renderWorkload,
}: {
  envName: string;
  workloads: AstroliftWorkload[];
  loading: boolean;
  renderWorkload: (workload: AstroliftWorkload) => React.ReactNode;
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
            <React.Fragment key={`${envName}-${w.id}`}>{renderWorkload(w)}</React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

export type WorkloadOpsRowViewProps = ReturnType<typeof useWorkloadOps>;

// V1 hard ceiling — env overrides only tighten this; the server
// is authoritative on validation.
const HARD_UPPER = 20;
const HARD_LOWER = 0;

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
export function WorkloadOpsRowView({
  envName,
  workload,
  restarting,
  scaling,
  onRestart,
  onApply,
}: WorkloadOpsRowViewProps) {
  const t = useTranslations("apps.settings.controls");

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

  async function handleRestart() {
    await onRestart();
    setRestartConfirmOpen(false);
  }

  async function handleApply() {
    // Revert local pending so the displayed counter matches reality
    // on validation failure.
    if (!(await onApply(pending))) setPending(initial);
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
