"use client";

import { useTranslations, useFormatter } from "next-intl";

import {
  CheckIcon,
  CopyIcon,
  FilmIcon,
  HistoryIcon,
  MoreHorizontalIcon,
  WorkflowIcon,
  XCircleIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Panel } from "@/components/panel/Panel";
import { RunPage } from "@/components/run/RunPage";
import type { StepState, TimelineStep } from "@/components/run/Timeline";
import { formatClock } from "@/components/run/format";
import { RunMissing } from "@/components/screens/jobs/RunDetailParts";
import { RunOutcomeCell } from "@/components/screens/tasks/RunsScreen";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { RUN_REPLAY_LEGEND, RunReplay, VizLegend, WorkflowView } from "@/components/viz";
import type { WorkflowStageExecution } from "@/graphql/workflows/tiered.types";
import type { WorkflowHistoryEvent } from "@/graphql/workflows/workflows.types";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import { GateCardView, upstreamResult } from "./GateReview";
import type { GateDecision } from "./use-gate-review";
import {
  type ExecState,
  type PlanStage,
  runLogLines,
  runReplayTimeline,
  runRounds,
  runSnapshot,
  type RunStepItem,
  type WorkflowRunSubject,
} from "./workflow-run-model";
import { workflowTabHref } from "./workflow-tabs-model";

export interface WorkflowRunScreenProps {
  slug: string;
  workflowName: string;
  runId: string;
  /** Null while it loads, or when no run of this workflow has the id. */
  run: WorkflowRunSubject | null;
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** The run was looked for in a window of recent runs, not by id: not found may mean older. */
  windowed?: boolean;
  /** The definition's stages, in order. */
  plan: PlanStage[];
  executions: WorkflowStageExecution[];
  executionsLoading: boolean;
  executionsError: string | null;
  onRetryExecutions: () => void;
  /** The engine's history of the run. */
  history: WorkflowHistoryEvent[];
  historyLoading: boolean;
  historyError: string | null;
  onRetryHistory: () => void;
  onDownloadLog: () => void;
  /** Ticks while the run is live. */
  now: number;
  motion: "full" | "reduced";
  /** Execution ids of the gates the viewer may decide; null when that is unknown (the server still checks). */
  viewerGateIds: string[] | null;
  decide: (gate: WorkflowStageExecution, decision: GateDecision, note: string) => Promise<boolean>;
  decidingGuids: string[];
  /** The workflows run grant, and the run reached the engine. */
  canCancel: boolean;
  cancelling: boolean;
  onCancel: () => Promise<boolean>;
}

const STEP: Record<ExecState, StepState> = {
  pending: "pending",
  running: "running",
  waiting: "running",
  ok: "ok",
  failed: "failed",
  skipped: "skipped",
};

const DOT: Record<ExecState, "ok" | "warn" | "error" | "muted" | "pending"> = {
  pending: "muted",
  running: "pending",
  waiting: "warn",
  ok: "ok",
  failed: "error",
  skipped: "muted",
};

/** `Agents ▾ › Workflows › <workflow> › run <id8>` (spec 44 §4.4): four crumbs. */
export function workflowRunCrumbs(
  slug: string,
  workflowName: string,
  runId: string,
  t: ReturnType<typeof useTranslations<"workflowRun">>
) {
  return [
    areaSwitcher(NAV, "agents", "workflows"),
    { label: t("workflows"), href: "/workflows" },
    { label: workflowName, href: workflowTabHref(slug, "runs") },
    { label: t("runTitle", { id: runId.slice(0, 8) }) },
  ];
}

function BranchList({ item }: { item: RunStepItem }) {
  const t = useTranslations("workflowBounds");
  const tr = useTranslations("workflowRun");
  const format = useFormatter();
  const settled = item.branches.filter((b) => b.state === "ok" || b.state === "failed").length;
  return (
    <span className="mt-1 flex min-w-0 flex-col gap-1">
      <span className="font-mono">
        {tr("branchesSettled", {
          settled,
          total: item.plannedBranches ?? item.branches.length,
        })}
      </span>
      <span
        className="flex min-w-0 flex-col gap-0.5"
        role="list"
        aria-label={tr("branchesAria", { name: item.name })}
      >
        {item.branches.map((b) => (
          <span key={b.id} role="listitem" className="flex min-w-0 items-start gap-2">
            <StatusDot status={DOT[b.state]} className="mt-1" />
            <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
              {b.label}
              {b.startedAt && (
                <span className="text-muted-foreground block">
                  {t("branchStarted", {
                    time: format.dateTime(new Date(b.startedAt), {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    }),
                  })}
                </span>
              )}
              {b.error && <span className="text-danger-fg font-mono"> · {b.error}</span>}
            </span>
            {b.durationMs != null && (
              <span className="shrink-0 font-mono tabular-nums">{formatClock(b.durationMs)}</span>
            )}
          </span>
        ))}
      </span>
    </span>
  );
}

/**
 * One workflow run on the run archetype (spec 44 §5.5): the stage timeline
 * on the left, grouped by round when the run looped, each fan-out with its
 * branches; the log (stage events and the engine history) on the right.
 * A gate waiting on a decision is reviewed in place in the timeline, and
 * when it waits on the viewer, Approve is the page's primary action (Reject
 * in `⋯`); otherwise a live run's primary action is Stop. Under the run, the
 * workflow view of this run and the replay scrubber (with GIF and video
 * export), each with its legend. Pure; the data half is useWorkflowRunPage.
 */
export function WorkflowRunScreen({
  slug,
  workflowName,
  runId,
  run,
  loading,
  error,
  onRetry,
  windowed = false,
  plan,
  executions,
  executionsLoading,
  executionsError,
  onRetryExecutions,
  history,
  historyLoading,
  historyError,
  onRetryHistory,
  onDownloadLog,
  now,
  motion,
  viewerGateIds,
  decide,
  decidingGuids,
  canCancel,
  cancelling,
  onCancel,
}: WorkflowRunScreenProps) {
  const [confirmCancel, setConfirmCancel] = React.useState(false);
  const tBounds = useTranslations("workflowBounds");
  const t = useTranslations("workflowRun");
  const title = t("runTitle", { id: runId.slice(0, 8) });
  const crumbs = workflowRunCrumbs(slug, workflowName, runId, t);

  if (!run && loading) {
    return (
      <RunPage
        crumbs={crumbs}
        title={title}
        steps={[]}
        stepsLoading
        log={{ lines: [], loading: true }}
      />
    );
  }
  if (!run) {
    return (
      <RunMissing
        crumbs={crumbs}
        title={t("workflowRun")}
        icon={<HistoryIcon className="size-4" />}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <HistoryIcon className="size-5" />,
          title: t("notFoundTitle"),
          description: windowed ? t("notFoundWindowed") : t("notFoundDescription"),
          actionHref: workflowTabHref(slug, "runs"),
          actionLabel: t("openRuns"),
        }}
      />
    );
  }

  const rounds = runRounds(plan, executions, run, now);
  const looped = rounds.length > 1;
  const mayDecide = (gate: WorkflowStageExecution) =>
    viewerGateIds === null || viewerGateIds.includes(gate.executionId);
  const gates = rounds.flatMap((r) => r.items).flatMap((i) => (i.gate ? [i.gate] : []));
  const viewerGate = gates.find(mayDecide) ?? null;

  const steps: TimelineStep[] = rounds.flatMap((r) => {
    const items = r.items.map(
      (item): TimelineStep => ({
        id: item.id,
        name:
          item.attempt > 1
            ? t("stepAttempt", { name: item.name, attempt: item.attempt })
            : item.name,
        state: STEP[item.state],
        durationMs: item.durationMs,
        detail: (
          <StepDetail
            item={item}
            gate={
              item.gate ? (
                <GateCardView
                  key={item.gate.guid}
                  gate={item.gate}
                  upstream={upstreamResult(item.gate, executions)}
                  loading={decidingGuids.includes(item.gate.guid)}
                  canDecide={mayDecide(item.gate)}
                  onDecide={(decision, note) => decide(item.gate!, decision, note)}
                />
              ) : null
            }
          />
        ),
      })
    );
    if (!looped) return items;
    return [
      {
        id: `round-${r.round}`,
        name:
          r.maxRounds && r.edgeRound === r.round
            ? tBounds("roundBound", { round: r.round, max: r.maxRounds })
            : tBounds("roundTitle", { round: r.round }),
        state: STEP[r.state],
        detail: r.cause
          ? `${r.cause}${r.maxRounds && r.edgeRound !== r.round ? ` · ${tBounds("edgeBound", { edgeRound: r.edgeRound!, max: r.maxRounds })}` : ""}`
          : r.round === 1
            ? t("firstPass")
            : undefined,
      },
      ...items,
    ];
  });

  const failedItem = rounds
    .flatMap((r) => r.items)
    .filter((i) => i.state === "failed")
    .at(-1);
  const failure =
    run.outcome === "failed"
      ? {
          title: t("runFailed"),
          reason:
            failedItem?.error ??
            failedItem?.branches.find((b) => b.error)?.error ??
            (failedItem ? t("stepFailed", { name: failedItem.name }) : t("noReason")),
        }
      : null;

  const lines = runLogLines(plan, executions, history);
  const replay = runReplayTimeline(plan, executions, run, `${workflowName} · ${title}`);
  const snapshot = runSnapshot(plan, executions, run, { lineId: slug, name: workflowName, now });
  const durationEnd = run.endedAt ? Date.parse(run.endedAt) : run.live ? now : null;
  const durationMs =
    run.startedAt && durationEnd !== null
      ? Math.max(0, durationEnd - Date.parse(run.startedAt))
      : null;

  const stop = run.live && canCancel;
  const primaryAction = viewerGate ? (
    <Button
      size="sm"
      disabled={decidingGuids.includes(viewerGate.guid)}
      onClick={() => void decide(viewerGate, "approved", "")}
    >
      <CheckIcon className="size-4" />
      {t("approve")}
    </Button>
  ) : stop ? (
    <Button
      size="sm"
      variant="outline"
      disabled={cancelling}
      onClick={() => setConfirmCancel(true)}
    >
      <XCircleIcon className="size-4" />
      {cancelling ? t("stopping") : t("stop")}
    </Button>
  ) : undefined;

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-8" aria-label={t("moreActions")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        {viewerGate && (
          <DropdownMenuItem onSelect={() => void decide(viewerGate, "rejected", "")}>
            <XIcon className="size-4" />
            {t("reject")}
          </DropdownMenuItem>
        )}
        {viewerGate && stop && (
          <DropdownMenuItem onSelect={() => setConfirmCancel(true)}>
            <XCircleIcon className="size-4" />
            {t("stopRun")}
          </DropdownMenuItem>
        )}
        {viewerGate && <DropdownMenuSeparator />}
        <DropdownMenuItem asChild>
          <Link href={workflowTabHref(slug, "runs")}>
            <HistoryIcon className="size-4" />
            {t("allRuns")}
          </Link>
        </DropdownMenuItem>
        {run.parentRunGuid && (
          <DropdownMenuItem asChild>
            <Link href={`/tasks?kind=workflow&q=${encodeURIComponent(run.parentRunGuid)}`}>
              <WorkflowIcon className="size-4" />
              {t("findParent")}
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          onSelect={() => {
            navigator.clipboard
              .writeText(run.guid)
              .then(() => toast.success(t("idCopied")))
              .catch(() => toast.error(t("copyFailed")));
          }}
        >
          <CopyIcon className="size-4" />
          {t("copyId")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <RunPage
        crumbs={crumbs}
        title={title}
        status={<RunOutcomeCell run={run} />}
        durationMs={durationMs}
        context={
          run.temporalWorkflowId ? (
            <span className="font-mono [overflow-wrap:anywhere]" title={run.temporalWorkflowId}>
              {run.temporalWorkflowId}
            </span>
          ) : (
            t("notReachedEngine")
          )
        }
        primaryAction={primaryAction}
        menu={menu}
        steps={steps}
        stepsLoading={executionsLoading}
        stepsError={executionsError}
        onRetrySteps={onRetryExecutions}
        failure={failure}
        log={{
          lines,
          loading: historyLoading && executions.length === 0,
          error: lines.length === 0 ? historyError : null,
          onRetry: onRetryHistory,
          onDownload: lines.length > 0 ? onDownloadLog : undefined,
          emptyHint: !run.temporalWorkflowId
            ? t("logNeverReached")
            : run.live
              ? t("logNoEventsYet")
              : t("logNoEvents"),
        }}
      />

      {snapshot.lines.length > 0 && (
        <WorkflowView
          snapshot={snapshot}
          title={t("workflowView")}
          description={t("workflowViewDescription")}
          motion={motion}
          stationLinks={Object.fromEntries(
            plan.map((stage) => {
              const execution = [...executions]
                .reverse()
                .find((row) => row.stageOrder === stage.order && row.agentRunGuid);
              return [
                `${slug}:o${stage.order}`,
                execution?.agentRunGuid
                  ? `/agents/runs/${encodeURIComponent(execution.agentRunGuid)}`
                  : workflowTabHref(slug, "build"),
              ];
            })
          )}
        />
      )}

      <Panel
        title={t("replay")}
        icon={<FilmIcon className="size-4" />}
        description={t("replayDescription")}
        empty={
          replay
            ? null
            : {
                icon: <FilmIcon className="size-5" />,
                title: t("replayEmpty"),
              }
        }
      >
        {replay && (
          <div className="flex min-w-0 flex-col gap-3">
            <RunReplay timeline={replay} motion={motion} now={run.live ? now : undefined} />
            <VizLegend items={RUN_REPLAY_LEGEND} motion={motion} />
          </div>
        )}
      </Panel>

      {stop && (
        <ConfirmDialog
          open={confirmCancel}
          onOpenChange={setConfirmCancel}
          title={t("stopTitle")}
          description={t("stopDescription")}
          confirmLabel={t("stopRun")}
          cancelLabel={t("keepRunning")}
          destructive
          onConfirm={async () => {
            if (!(await onCancel())) throw new Error(t("notStopped"));
          }}
        />
      )}
    </div>
  );
}

function StepDetail({ item, gate }: { item: RunStepItem; gate: React.ReactNode }) {
  const collectionT = useTranslations("workflowCollections");
  const t = useTranslations("workflowRun");
  const parts: React.ReactNode[] = [];
  if (item.collectionItem != null)
    parts.push(<span key="collection">{collectionT("item", { index: item.collectionItem })}</span>);
  if (item.cause) parts.push(<span key="cause">{item.cause}</span>);
  if (item.error && item.branches.length === 0)
    parts.push(
      <span key="error" className="font-mono">
        {item.error}
      </span>
    );
  if (item.planned && item.plannedBranches)
    parts.push(
      <span key="width" className="font-mono">
        {t("branchesPlanned", { count: item.plannedBranches })}
      </span>
    );
  if (item.childHref)
    parts.push(
      <Link key="child" href={item.childHref} className="underline underline-offset-2">
        {t("openChild")}
      </Link>
    );
  if (item.branches.length > 0) parts.push(<BranchList key="branches" item={item} />);
  if (gate)
    parts.push(
      <span key="gate" className="text-foreground mt-2 block font-sans text-sm">
        {gate}
      </span>
    );
  if (parts.length === 0) return null;
  return <span className="flex min-w-0 flex-col gap-0.5">{parts}</span>;
}
