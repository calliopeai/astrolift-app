/**
 * One workflow run, read four ways (spec 44 §5.5): the stage timeline
 * grouped by round with fan-out branches, the log, the replay scrubber's
 * timeline and the workflow view's snapshot. Pure.
 *
 * The backend reports stage executions, not rounds or branches, so both are
 * derived here: executions are taken in start order, one at an earlier stage
 * than the round already reached opens the next round (a loop sent work
 * back), a second attempt at the same stage is a retry, and the executions
 * of a fan-out stage within a round are its branches.
 */
import type { LogLine } from "@/components/run/LogView";
import {
  outcomeOf,
  type RunOutcome,
} from "@/components/screens/administration/insights/combined-runs";
import type {
  FanoutBranch,
  RunTimeline,
  StationKind,
  WorkflowLine,
  WorkflowSnapshot,
  WorkflowStation,
  WorkflowTrain,
} from "@/components/viz/core/workflow-model";
import type {
  TieredWorkflowRun,
  WorkflowDefinitionRun,
  WorkflowStageExecution,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";
import type { WorkflowHistoryEvent } from "@/graphql/workflows/workflows.types";

import { isRunTerminal } from "./workflow-run-state";

// ---------------------------------------------------------------------------
// The run, whichever kind of workflow it belongs to
// ---------------------------------------------------------------------------

export interface WorkflowRunSubject {
  guid: string;
  /** The source's own status word. */
  status: string;
  outcome: RunOutcome;
  live: boolean;
  /** The engine identity its stage executions and history key on. */
  temporalWorkflowId: string | null;
  temporalRunId: string | null;
  startedAt: string | null;
  endedAt: string | null;
  parentRunGuid: string | null;
  childRunCount: number;
  /** Where the engine says the run is, when the source reports it. */
  currentStageOrder: number | null;
}

/** `/workflows/<slug>/runs/<guid>`: one run's page. */
export function workflowRunHref(slug: string, guid: string): string {
  return `/workflows/${encodeURIComponent(slug)}/runs/${encodeURIComponent(guid)}`;
}

const failedWord = (s: string) =>
  s.includes("fail") || s.includes("error") || s.includes("timed_out");

/** A configured workflow's run: its state is free text, so the terminal check is the backstop. */
export function configuredRunOutcome(run: TieredWorkflowRun): RunOutcome {
  const known = outcomeOf("workflow", run.currentState);
  if (known !== "unknown") return known;
  if (!isRunTerminal(run)) return "running";
  const s = run.currentState.toLowerCase();
  if (failedWord(s)) return "failed";
  if (s.includes("cancel") || s.includes("terminat")) return "cancelled";
  return "succeeded";
}

export function configuredRunSubject(run: TieredWorkflowRun): WorkflowRunSubject {
  return {
    guid: run.guid,
    status: run.currentState,
    outcome: configuredRunOutcome(run),
    live: !isRunTerminal(run),
    temporalWorkflowId: run.temporalWorkflowId,
    temporalRunId: run.temporalRunId,
    startedAt: run.startedAt || null,
    endedAt: run.completedAt,
    parentRunGuid: null,
    childRunCount: 0,
    currentStageOrder: null,
  };
}

export function definitionRunSubject(run: WorkflowDefinitionRun): WorkflowRunSubject {
  const outcome = outcomeOf("workflow", run.status);
  return {
    guid: run.guid,
    status: run.status,
    outcome,
    live: outcome === "running" || outcome === "waiting",
    temporalWorkflowId: run.temporalWorkflowId || null,
    temporalRunId: run.temporalRunId,
    startedAt: run.startedAt,
    endedAt: run.endedAt,
    parentRunGuid: run.parentRunGuid,
    childRunCount: run.childRunCount,
    currentStageOrder: run.currentStageOrder,
  };
}

// ---------------------------------------------------------------------------
// Executions, placed by round, attempt and branch
// ---------------------------------------------------------------------------

/** The planned stage, as much of the definition's topology as a run needs. */
export type PlanStage = Pick<
  WorkflowTopologyStage,
  "order" | "kind" | "role" | "agentName" | "workflowRef" | "fanOutCount" | "fanOutDynamic"
>;

export type ExecState = "pending" | "running" | "waiting" | "ok" | "failed" | "skipped";

export interface PlacedExecution {
  execution: WorkflowStageExecution;
  name: string;
  state: ExecState;
  round: number;
  attempt: number;
  /** A fan-out's branch: which one, 1-based. */
  branch: number | null;
  /** Why this execution happened again: the loop or retry, in words. */
  causedBy: { loopId: string; reason: string } | null;
}

function humanize(kind: string): string {
  if (!kind) return "Stage";
  return kind.charAt(0).toUpperCase() + kind.slice(1).replace(/_/g, " ");
}

const isGateKind = (kind: string) => kind === "human_gate" || kind === "gate";

/** A stage's name: its agent, its role, the child workflow, or its kind. */
export function stageName(plan: PlanStage | null, execution?: WorkflowStageExecution): string {
  const kind = plan?.kind || execution?.stageKind || "";
  const role = plan?.role || execution?.stageRole || "";
  if (isGateKind(kind)) return role ? `Gate · ${role}` : "Gate";
  if (plan?.agentName) return plan.agentName;
  if (kind === "workflow" && plan?.workflowRef) return `Workflow · ${plan.workflowRef}`;
  if (kind === "workflow" && execution?.childWorkflowDefinitionSlug)
    return `Workflow · ${execution.childWorkflowDefinitionSlug}`;
  return role || humanize(kind);
}

const isFan = (plan: PlanStage | null) =>
  Boolean(plan && ((plan.fanOutCount != null && plan.fanOutCount > 1) || plan.fanOutDynamic));

/** An execution's state; a gate waiting on a decision is `waiting`. */
export function execState(x: WorkflowStageExecution): ExecState {
  if (isGateKind(x.stageKind) && x.humanGateState === "pending") return "waiting";
  const s = x.status.toLowerCase();
  if (["running", "in_progress", "started", "active"].includes(s)) return "running";
  if (["pending", "queued", "scheduled"].includes(s)) return x.startedAt ? "running" : "pending";
  if (["completed", "succeeded", "success", "approved"].includes(s)) return "ok";
  if (["failed", "error", "timed_out", "rejected", "denied"].includes(s)) return "failed";
  if (["cancelled", "canceled", "skipped", "terminated"].includes(s)) return "skipped";
  return x.endedAt ? "ok" : x.startedAt ? "running" : "pending";
}

const STATE_WORD: Record<ExecState, string> = {
  pending: "pending",
  running: "running",
  waiting: "waiting for approval",
  ok: "succeeded",
  failed: "failed",
  skipped: "cancelled",
};

const ms = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
};

function startOf(x: WorkflowStageExecution): number {
  return ms(x.startedAt) ?? ms(x.createdAt) ?? Number.POSITIVE_INFINITY;
}

function byStart(a: WorkflowStageExecution, b: WorkflowStageExecution): number {
  const d = startOf(a) - startOf(b);
  if (d !== 0 && Number.isFinite(d)) return d;
  const ea = Number(a.executionId);
  const eb = Number(b.executionId);
  if (Number.isFinite(ea) && Number.isFinite(eb) && ea !== eb) return ea - eb;
  return a.guid.localeCompare(b.guid);
}

function planByOrder(plan: PlanStage[]): Map<number, PlanStage> {
  return new Map(plan.map((s) => [s.order, s]));
}

/** Every execution with its round, attempt, branch and cause, in start order. */
export function placeExecutions(
  plan: PlanStage[],
  executions: WorkflowStageExecution[]
): PlacedExecution[] {
  const stages = planByOrder(plan);
  const out: PlacedExecution[] = [];
  const branches = new Map<string, number>();
  let round = 1;
  let reached = -1;
  for (const x of [...executions].sort(byStart)) {
    const def = stages.get(x.stageOrder) ?? null;
    const fan = isFan(def);
    const prev = out[out.length - 1] ?? null;
    let causedBy: PlacedExecution["causedBy"] = null;
    if (x.stageOrder < reached) {
      round += 1;
      reached = -1;
      const why = prev
        ? `${prev.name} ${STATE_WORD[prev.state]}${prev.execution.errorMessage ? `: ${prev.execution.errorMessage}` : ""}`
        : "Sent back";
      causedBy = {
        loopId: `back:${prev?.execution.stageOrder ?? "?"}->${x.stageOrder}`,
        reason: why,
      };
    } else if (x.attemptNumber > 1) {
      const failed = [...out]
        .reverse()
        .find((p) => p.round === round && p.execution.stageOrder === x.stageOrder);
      const err = failed?.execution.errorMessage;
      causedBy = {
        loopId: `retry:${x.stageOrder}`,
        reason: `Attempt ${x.attemptNumber}${err ? ` after: ${err}` : ""}`,
      };
    }
    reached = Math.max(reached, x.stageOrder);
    let branch: number | null = null;
    if (fan) {
      const key = `${round}:${x.stageOrder}`;
      branch = (branches.get(key) ?? 0) + 1;
      branches.set(key, branch);
    }
    out.push({
      execution: x,
      name: stageName(def, x),
      state: execState(x),
      round,
      attempt: Math.max(1, x.attemptNumber),
      branch,
      causedBy,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// The stage timeline, grouped by round
// ---------------------------------------------------------------------------

export interface RunBranchItem {
  id: string;
  label: string;
  state: ExecState;
  durationMs: number | null;
  error: string | null;
}

export interface RunStepItem {
  id: string;
  name: string;
  state: ExecState;
  durationMs: number | null;
  attempt: number;
  error: string | null;
  /** Why it ran again (a retry or a loop), in words. */
  cause: string | null;
  /** A fan-out's branches; empty for any other stage. */
  branches: RunBranchItem[];
  /** The fan-out's planned width, when the definition fixes it. */
  plannedBranches: number | null;
  /** The execution, while a gate waits on a decision. */
  gate: WorkflowStageExecution | null;
  /** A nested workflow's child run. */
  childHref: string | null;
  /** Planned, never executed in this run. */
  planned: boolean;
}

export interface RunRound {
  round: number;
  /** What sent the run into this round. Null for the first. */
  cause: string | null;
  state: ExecState;
  items: RunStepItem[];
}

function spanOf(x: WorkflowStageExecution, now: number): number | null {
  const a = ms(x.startedAt);
  if (a === null) return null;
  const b =
    ms(x.endedAt) ?? (execState(x) === "running" || execState(x) === "waiting" ? now : null);
  return b === null ? null : Math.max(0, b - a);
}

/** One state for several: failed, then waiting, running, pending, skipped, else ok. */
export function worstState(states: ExecState[]): ExecState {
  for (const s of ["failed", "waiting", "running", "pending"] as const)
    if (states.includes(s)) return s;
  if (states.length > 0 && states.every((s) => s === "skipped")) return "skipped";
  return "ok";
}

function childHref(x: WorkflowStageExecution, def: PlanStage | null): string | null {
  const slug = x.childWorkflowDefinitionSlug || def?.workflowRef || "";
  if (!slug) return null;
  return x.childWorkflowRunGuid
    ? workflowRunHref(slug, x.childWorkflowRunGuid)
    : `/workflows/${encodeURIComponent(slug)}/runs`;
}

/**
 * The run's stages by round. Within a round each stage is one item, in
 * the order it started; a fan-out's executions are its branches. The
 * planned stages the run has not reached close the last round: pending
 * while it runs, skipped once it ended without them.
 */
export function runRounds(
  plan: PlanStage[],
  executions: WorkflowStageExecution[],
  run: Pick<WorkflowRunSubject, "live">,
  now: number
): RunRound[] {
  const stages = planByOrder(plan);
  const placed = placeExecutions(plan, executions);
  const rounds: RunRound[] = [];
  for (const p of placed) {
    let r = rounds[rounds.length - 1];
    if (!r || r.round !== p.round) {
      r = { round: p.round, cause: p.causedBy?.reason ?? null, state: "ok", items: [] };
      rounds.push(r);
    }
    const x = p.execution;
    const def = stages.get(x.stageOrder) ?? null;
    if (p.branch !== null) {
      const id = `r${p.round}:o${x.stageOrder}`;
      let item = r.items.find((i) => i.id === id);
      if (!item) {
        item = {
          id,
          name: p.name,
          state: "ok",
          durationMs: null,
          attempt: 1,
          error: null,
          cause: null,
          branches: [],
          plannedBranches: def?.fanOutDynamic ? null : (def?.fanOutCount ?? null),
          gate: null,
          childHref: null,
          planned: false,
        };
        r.items.push(item);
      }
      item.branches.push({
        id: x.guid,
        label: `Branch ${p.branch}`,
        state: p.state,
        durationMs: spanOf(x, now),
        error: x.errorMessage || null,
      });
      continue;
    }
    r.items.push({
      id: x.guid,
      name: p.name,
      state: p.state,
      durationMs: spanOf(x, now),
      attempt: p.attempt,
      error: x.errorMessage || null,
      cause: p.round > 1 && r.items.length === 0 ? null : (p.causedBy?.reason ?? null),
      branches: [],
      plannedBranches: null,
      gate: p.state === "waiting" ? x : null,
      childHref: x.stageKind === "workflow" || def?.kind === "workflow" ? childHref(x, def) : null,
      planned: false,
    });
  }

  // A fan-out's own state and span come from its branches.
  const branchesOf = new Map<string, WorkflowStageExecution[]>();
  for (const p of placed)
    if (p.branch !== null) {
      const id = `r${p.round}:o${p.execution.stageOrder}`;
      branchesOf.set(id, [...(branchesOf.get(id) ?? []), p.execution]);
    }
  for (const r of rounds)
    for (const item of r.items) {
      const xs = branchesOf.get(item.id);
      if (!xs) continue;
      item.state = worstState(item.branches.map((b) => b.state));
      const starts = xs.map((x) => ms(x.startedAt)).filter((t): t is number => t !== null);
      const ends = xs.map((x) => ms(x.endedAt));
      const open = ends.some((e) => e === null);
      if (starts.length > 0) {
        const a = Math.min(...starts);
        const b = open
          ? item.state === "running" || item.state === "waiting"
            ? now
            : null
          : Math.max(...(ends as number[]));
        item.durationMs = b === null ? null : Math.max(0, b - a);
      }
    }

  // The planned stages after the last one reached.
  const last = rounds[rounds.length - 1];
  const reached = last
    ? Math.max(...placed.filter((p) => p.round === last.round).map((p) => p.execution.stageOrder))
    : -1;
  const rest = [...stages.values()]
    .filter((s) => s.order > reached)
    .sort((a, b) => a.order - b.order);
  if (rest.length > 0) {
    const target = last ?? { round: 1, cause: null, state: "ok" as ExecState, items: [] };
    if (!last) rounds.push(target);
    for (const s of rest)
      target.items.push({
        id: `plan:${s.order}`,
        name: stageName(s),
        state: run.live ? "pending" : "skipped",
        durationMs: null,
        attempt: 1,
        error: null,
        cause: null,
        branches: [],
        plannedBranches: isFan(s) && !s.fanOutDynamic ? s.fanOutCount : null,
        gate: null,
        childHref: null,
        planned: true,
      });
  }

  for (const r of rounds)
    r.state = worstState(r.items.filter((i) => !i.planned).map((i) => i.state));
  return rounds;
}

// ---------------------------------------------------------------------------
// The log
// ---------------------------------------------------------------------------

/** Stage starts and ends, with their errors, and the engine's history, in time order. */
export function runLogLines(
  plan: PlanStage[],
  executions: WorkflowStageExecution[],
  history: WorkflowHistoryEvent[]
): LogLine[] {
  const lines: (LogLine & { at: number })[] = [];
  for (const p of placeExecutions(plan, executions)) {
    const x = p.execution;
    const label = `${p.name}${p.branch !== null ? ` · branch ${p.branch}` : ""}${p.attempt > 1 ? ` · attempt ${p.attempt}` : ""}${p.round > 1 ? ` · round ${p.round}` : ""}`;
    const start = ms(x.startedAt);
    if (start !== null)
      lines.push({ at: start, ts: start, level: "info", message: `${label} started` });
    const end = ms(x.endedAt);
    if (p.state === "waiting" && start !== null) {
      const who = x.stageApprovers?.length ? ` by ${x.stageApprovers.join(", ")}` : "";
      lines.push({
        at: start,
        ts: start,
        level: "warn",
        message: `${label} waiting for approval${who}`,
      });
    }
    if (end !== null)
      lines.push({
        at: end,
        ts: end,
        level: p.state === "failed" ? "error" : p.state === "skipped" ? "warn" : "info",
        message: `${label} ${STATE_WORD[p.state]}${x.errorMessage ? `: ${x.errorMessage}` : ""}`,
      });
  }
  for (const ev of history) {
    const at = ms(ev.timestamp);
    if (at === null) continue;
    const d = ev.decision.toLowerCase();
    lines.push({
      at,
      ts: at,
      level: d === "failed" || d === "timed_out" ? "error" : d === "cancelled" ? "warn" : "debug",
      message: `engine ${ev.eventType}${ev.decision ? ` · ${ev.decision}` : ""}${ev.retryCount > 1 ? ` · retry ${ev.retryCount}` : ""}`,
    });
  }
  return lines
    .sort((a, b) => a.at - b.at)
    .map(({ ts, level, message }) => ({ ts, level, message }));
}

// ---------------------------------------------------------------------------
// The replay's timeline
// ---------------------------------------------------------------------------

const REPLAY_STATUS: Record<ExecState, RunTimeline["stages"][number]["status"]> = {
  pending: "running",
  running: "running",
  waiting: "waiting",
  ok: "succeeded",
  failed: "failed",
  skipped: "skipped",
};

function stationKind(kind: string, fan: boolean): StationKind {
  if (fan) return "fanout";
  if (isGateKind(kind)) return "gate";
  if (kind === "workflow") return "workflow";
  if (kind === "aggregation") return "join";
  return "stage";
}

/**
 * The replay's run: each started execution on real time, by round, with a
 * fan-out as one main-track span and its branches on lanes of their own.
 * Null before any stage started.
 */
export function runReplayTimeline(
  plan: PlanStage[],
  executions: WorkflowStageExecution[],
  run: WorkflowRunSubject,
  label: string
): RunTimeline | null {
  const stages = planByOrder(plan);
  const placed = placeExecutions(plan, executions).filter(
    (p) => ms(p.execution.startedAt) !== null
  );
  if (placed.length === 0) return null;
  const out: RunTimeline["stages"] = [];
  const fanSpans = new Map<string, RunTimeline["stages"][number]>();
  for (const p of placed) {
    const x = p.execution;
    const start = ms(x.startedAt)!;
    const end = ms(x.endedAt);
    const def = stages.get(x.stageOrder) ?? null;
    if (p.branch !== null) {
      const key = `r${p.round}:o${x.stageOrder}`;
      let span = fanSpans.get(key);
      if (!span) {
        span = {
          id: `fan:${key}`,
          name: p.name,
          kind: "fanout",
          startedAt: start,
          finishedAt: end,
          status: REPLAY_STATUS[p.state],
          round: p.round,
          attempt: 1,
        };
        fanSpans.set(key, span);
        out.push(span);
      } else {
        span.startedAt = Math.min(span.startedAt, start);
        span.finishedAt =
          span.finishedAt === null || end === null ? null : Math.max(span.finishedAt, end);
      }
      out.push({
        id: x.guid,
        name: `${p.name} · branch ${p.branch}`,
        kind: "stage",
        startedAt: start,
        finishedAt: end,
        status: REPLAY_STATUS[p.state],
        round: p.round,
        attempt: p.attempt,
        branchId: `${key}:b${p.branch}`,
      });
      continue;
    }
    out.push({
      id: x.guid,
      name: p.name,
      kind: stationKind(def?.kind || x.stageKind, false),
      startedAt: start,
      finishedAt: end,
      status: REPLAY_STATUS[p.state],
      round: p.round,
      attempt: p.attempt,
      causedBy: p.causedBy ?? undefined,
    });
  }
  // A fan-out span takes its branches' worst status.
  for (const [key, span] of fanSpans) {
    const states = placed
      .filter((p) => p.branch !== null && `r${p.round}:o${p.execution.stageOrder}` === key)
      .map((p) => p.state);
    span.status = REPLAY_STATUS[worstState(states)];
  }
  const first = Math.min(...out.map((s) => s.startedAt));
  const startedAt = Math.min(ms(run.startedAt) ?? first, first);
  const lastEnd = Math.max(...out.map((s) => s.finishedAt ?? s.startedAt));
  const finishedAt = run.live ? null : Math.max(ms(run.endedAt) ?? lastEnd, lastEnd);
  return { runId: run.guid, label, startedAt, finishedAt, stages: out };
}

// ---------------------------------------------------------------------------
// The workflow view's snapshot
// ---------------------------------------------------------------------------

const BRANCH_STATE: Record<ExecState, FanoutBranch["state"]> = {
  pending: "queued",
  running: "running",
  waiting: "running",
  ok: "succeeded",
  failed: "failed",
  skipped: "failed",
};

/** The plan, or the stage orders the executions name when there is no plan. */
function planOrFromExecutions(
  plan: PlanStage[],
  executions: WorkflowStageExecution[]
): PlanStage[] {
  if (plan.length > 0) return [...plan].sort((a, b) => a.order - b.order);
  const seen = new Map<number, PlanStage>();
  for (const x of executions)
    if (!seen.has(x.stageOrder))
      seen.set(x.stageOrder, {
        order: x.stageOrder,
        kind: x.stageKind,
        role: x.stageRole ?? "",
        agentName: "",
        workflowRef: x.childWorkflowDefinitionSlug ?? "",
        fanOutCount: null,
        fanOutDynamic: false,
      });
  return [...seen.values()].sort((a, b) => a.order - b.order);
}

/**
 * The workflow as a line: a gate, a fan-out (with the given branches, or its
 * planned width queued), the aggregation that joins a fan-out, a nested
 * workflow, or a plain stage.
 */
export function workflowLine(
  plan: PlanStage[],
  lineId: string,
  name: string,
  branchesAt: (order: number) => FanoutBranch[] | null = () => null
): WorkflowLine {
  const stations: WorkflowStation[] = [];
  plan.forEach((s) => {
    const id = `${lineId}:o${s.order}`;
    const prev = stations[stations.length - 1];
    if (isFan(s)) {
      const planned = s.fanOutDynamic ? 0 : (s.fanOutCount ?? 0);
      stations.push({
        id,
        name: stageName(s),
        kind: "fanout",
        dynamic: s.fanOutDynamic || undefined,
        branches:
          branchesAt(s.order) ??
          Array.from({ length: planned }, (_, i) => ({
            id: `${id}:b${i + 1}`,
            label: `Branch ${i + 1}`,
            state: "queued" as const,
          })),
      });
    } else if (s.kind === "aggregation" && prev?.kind === "fanout") {
      stations.push({ id, name: stageName(s), kind: "join", waitsOn: stations.length - 1 });
    } else if (s.kind === "workflow") {
      stations.push({ id, name: stageName(s), kind: "workflow", childLineId: `${id}:child` });
    } else {
      stations.push({ id, name: stageName(s), kind: isGateKind(s.kind) ? "gate" : "stage" });
    }
  });
  return { id: lineId, name, stations };
}

const trainLabel = (guid: string) => `run ${guid.slice(0, 8)}`;

/**
 * One run on its workflow's line: where it is (the last stage its latest
 * round reached), a fan-out's branches as they stand, and whether it moves,
 * holds at a gate, failed or is done.
 */
export function runSnapshot(
  plan: PlanStage[],
  executions: WorkflowStageExecution[],
  run: WorkflowRunSubject,
  { lineId, name, now }: { lineId: string; name: string; now: number }
): WorkflowSnapshot {
  const stages = planOrFromExecutions(plan, executions);
  const placed = placeExecutions(stages, executions);
  const lastRound = placed.length > 0 ? placed[placed.length - 1]!.round : 1;
  const current = placed.filter((p) => p.round === lastRound);
  const branchesAt = (order: number) => {
    const xs = current.filter((p) => p.branch !== null && p.execution.stageOrder === order);
    return xs.length === 0
      ? null
      : xs.map((p) => ({
          id: p.execution.guid,
          label: `Branch ${p.branch}`,
          state: BRANCH_STATE[p.state],
        }));
  };
  const line = workflowLine(stages, lineId, name, branchesAt);
  const reachedOrder =
    current.length > 0 ? Math.max(...current.map((p) => p.execution.stageOrder)) : null;
  const reachedIndex =
    reachedOrder === null
      ? 0
      : Math.max(
          0,
          stages.findIndex((s) => s.order === reachedOrder)
        );
  const here = current.filter((p) => p.execution.stageOrder === reachedOrder);
  const hereState = worstState(here.map((p) => p.state));
  const station = line.stations[reachedIndex];
  const settled = here.filter(
    (p) => p.state === "ok" || p.state === "failed" || p.state === "skipped"
  );
  const done = !run.live && run.outcome === "succeeded";
  const train: WorkflowTrain = {
    id: run.guid,
    lineId,
    label: trainLabel(run.guid),
    at: done ? Math.max(0, line.stations.length - 1) : reachedIndex,
    progress: done
      ? 1
      : station?.kind === "fanout" && here.length > 0
        ? settled.length / here.length
        : 0,
    state: run.live
      ? hereState === "waiting"
        ? "held"
        : "moving"
      : run.outcome === "failed" || run.outcome === "cancelled"
        ? "failed"
        : "done",
    startedAt: ms(run.startedAt) ?? now,
    round: lastRound > 1 ? lastRound : undefined,
    attempt: here[0] && here[0].attempt > 1 ? here[0].attempt : undefined,
  };
  return {
    now,
    lines: line.stations.length > 0 ? [line] : [],
    trains: line.stations.length > 0 ? [train] : [],
    segments: [],
  };
}

/**
 * A workflow's live runs on its line, for the Runs tab. Only runs whose
 * position the source reports are placed: a run with no current stage has
 * no honest place on the line.
 */
export function liveRunsSnapshot(
  plan: PlanStage[],
  runs: WorkflowRunSubject[],
  { lineId, name, now }: { lineId: string; name: string; now: number }
): WorkflowSnapshot | null {
  const stages = [...plan].sort((a, b) => a.order - b.order);
  const line = workflowLine(stages, lineId, name);
  const trains: WorkflowTrain[] = [];
  for (const r of runs) {
    if (!r.live || r.currentStageOrder === null) continue;
    const at = stages.findIndex((s) => s.order === r.currentStageOrder);
    if (at < 0) continue;
    trains.push({
      id: r.guid,
      lineId,
      label: trainLabel(r.guid),
      at,
      progress: 0,
      state: line.stations[at]?.kind === "gate" ? "held" : "moving",
      startedAt: ms(r.startedAt) ?? now,
    });
  }
  if (line.stations.length === 0 || trains.length === 0) return null;
  return { now, lines: [line], trains, segments: [] };
}
