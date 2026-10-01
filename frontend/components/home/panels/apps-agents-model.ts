/**
 * The pure half of the Apps and Agents panels: ordering, the deploy and
 * run words, the reason a failure gives, and the chart buckets. The hooks
 * map GraphQL through these; the views and their stories take the results.
 * Tested in apps-agents-model.test.ts.
 */

import type { FleetAgentRow } from "@/components/screens/agents/detail/agent-fleet-snapshot";
import type {
  FleetAgent,
  FleetCluster,
  FleetRun,
  FleetSnapshot,
} from "@/components/viz/core/fleet-model";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";

const time = (iso: string | null | undefined) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? -Infinity : t;
};

/** Newest first; a row with no time sorts last. Stable for ties. */
export function newestFirst<T>(rows: T[], at: (row: T) => string | null | undefined): T[] {
  return rows
    .map((row, i) => ({ row, i, t: time(at(row)) }))
    .sort((a, b) => (b.t === a.t ? a.i - b.i : b.t > a.t ? 1 : -1))
    .map((x) => x.row);
}

/** The short name of a deploy: its commit, else its id, eight characters. */
export function deployShort(d: Pick<AstroliftDeployment, "commitSha" | "id">): string {
  return (d.commitSha || d.id).slice(0, 8);
}

export const deployHref = (id: string) => `/deployments/${encodeURIComponent(id)}`;
export const agentRunHref = (id: string) => `/agents/runs/${encodeURIComponent(id)}`;
export const agentHref = (slug: string) => `/agents/${encodeURIComponent(slug)}`;
export const appHref = (slug: string) => `/apps/${encodeURIComponent(slug)}`;
/** A workflow run on the workflow's Runs tab. */
export const workflowRunHref = (slug: string, runGuid: string) =>
  `/workflows/${encodeURIComponent(slug)}/runs?run=${encodeURIComponent(runGuid)}`;

const FAILED_DEPLOY = new Set(["failed", "rolled_back"]);

/**
 * Why a deploy failed, in the order the deploy page reads it: an abort's
 * reason, the status reason, the build error. The first line only; the
 * deploy page has the rest.
 */
export function deployReason(
  d: Pick<AstroliftDeployment, "status" | "abortedReason" | "statusReason" | "buildError">,
  fallback = { rolledBack: "Rolled back.", missing: "No reason was recorded for this failure." }
): string {
  const reason = d.abortedReason || d.statusReason || d.buildError;
  const first = reason?.split("\n")[0]?.trim();
  if (first) return first;
  return d.status === "rolled_back" ? fallback.rolledBack : fallback.missing;
}

/**
 * The apps and environments whose newest deploy failed or rolled back:
 * what is failing now, not every failure in the window.
 */
export function failingDeploys<
  D extends Pick<
    AstroliftDeployment,
    "registeredAppSlug" | "environmentName" | "status" | "createdAt" | "startedAt"
  >,
>(deployments: D[]): D[] {
  const latest = new Map<string, D>();
  for (const d of newestFirst(deployments, (x) => x.startedAt ?? x.createdAt)) {
    const key = `${d.registeredAppSlug}\u0000${d.environmentName}`;
    if (!latest.has(key)) latest.set(key, d);
  }
  return [...latest.values()].filter((d) => FAILED_DEPLOY.has(d.status));
}

/** A failed run's reason, or what to say when the run gave none. */
export function runReason(
  message: string | null | undefined,
  fallback = "No reason was recorded for this run."
): string {
  const first = message?.split("\n")[0]?.trim();
  return first || fallback;
}

/**
 * A failed workflow run's reason. The run list carries no error, so this
 * says where it stopped; the run page has the stage's error.
 */
export function workflowRunReason(
  r: Pick<WorkflowDefinitionRun, "status" | "currentStageOrder" | "currentStageRole">
): string {
  const stage =
    r.currentStageOrder != null
      ? `stage ${r.currentStageOrder}${r.currentStageRole ? ` (${r.currentStageRole})` : ""}`
      : null;
  const verb = r.status === "timed_out" ? "Timed out" : "Failed";
  return stage ? `${verb} at ${stage}.` : `${verb}.`;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** The UTC day an instant falls on, `YYYY-MM-DD`. */
export const utcDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);

export interface RunDay {
  /** `YYYY-MM-DD`, UTC. */
  date: string;
  runs: number;
  failed: number;
}

const FAILED_RUN = new Set(["failed", "timed_out", "error"]);

/**
 * Runs per UTC day over the last `days` days ending today, oldest first,
 * from the runs in hand. Runs outside the window are left out.
 */
export function runsPerDay(
  runs: { status: string; createdAt: string }[],
  now: number,
  days = 7
): RunDay[] {
  const out: RunDay[] = Array.from({ length: days }, (_, i) => ({
    date: utcDay(now - (days - 1 - i) * DAY_MS),
    runs: 0,
    failed: 0,
  }));
  const index = new Map(out.map((d, i) => [d.date, i]));
  for (const r of runs) {
    const t = Date.parse(r.createdAt);
    if (Number.isNaN(t)) continue;
    const i = index.get(utcDay(t));
    if (i === undefined) continue;
    out[i]!.runs += 1;
    if (FAILED_RUN.has(r.status.toLowerCase())) out[i]!.failed += 1;
  }
  return out;
}

/**
 * A cost trend laid onto the same days, zero where the trend has no point
 * (a day with no spend recorded).
 */
export function spendPerDay(
  trend: { date: string; amountCents: number }[],
  days: RunDay[]
): number[] {
  const byDay = new Map<string, number>();
  for (const p of trend) {
    const d = p.date.slice(0, 10);
    byDay.set(d, (byDay.get(d) ?? 0) + p.amountCents);
  }
  return days.map((d) => byDay.get(d.date) ?? 0);
}

/** The last sample's value and the mean, from a series of points. */
export function seriesFigures(values: number[]): { last: number | null; mean: number | null } {
  if (values.length === 0) return { last: null, mean: null };
  const sum = values.reduce((a, b) => a + b, 0);
  return { last: values[values.length - 1]!, mean: sum / values.length };
}

export type Dot = "ok" | "warn" | "error" | "muted" | "pending";

const DEPLOY_DOT: Record<string, Dot> = {
  running: "ok",
  failed: "error",
  rolled_back: "error",
  pending_approval: "warn",
  pending: "warn",
  deploying: "pending",
  redeploying: "pending",
  superseded: "muted",
};

/** A deploy status's dot, as DeploymentStatusPill colours it; unknown reads muted. */
export function deployDot(status: string | null | undefined): Dot {
  return (status && DEPLOY_DOT[status]) || "muted";
}

/** `pending_approval` as `Pending approval`. */
export function statusLabel(status: string): string {
  const words = status.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1).toLowerCase() : "";
}

export interface RunningTaskRow {
  id: string;
  agentSlug: string;
  startedAt?: string | null;
}

const NO_PROJECT = "__none";

/**
 * Running now as the fleet views draw it: only the agents with a run in
 * flight, grouped by project (the fleet read has no cluster per agent), and
 * the running runs in hand as the manifest. Nothing invented: no events,
 * every group healthy, no queue.
 */
export function runningSnapshot(
  agents: (FleetAgentRow & { slug: string })[],
  running: RunningTaskRow[],
  now: number,
  noProject = "No project"
): FleetSnapshot {
  const bySlug = new Map(agents.map((a) => [a.slug, a]));
  const perAgent = new Map<string, number>();
  for (const t of running) perAgent.set(t.agentSlug, (perAgent.get(t.agentSlug) ?? 0) + 1);
  const groups = new Map<string, FleetCluster>();
  const fleet: FleetAgent[] = [];
  for (const a of agents) {
    const active = Math.max(a.runningCount, perAgent.get(a.slug) ?? 0);
    if (active === 0) continue;
    const key = a.projectSlug || NO_PROJECT;
    if (!groups.has(key))
      groups.set(key, { id: key, name: a.projectSlug || noProject, health: "ok" });
    fleet.push({
      id: a.id,
      name: a.name,
      clusterId: key,
      health: "ok",
      load: Math.min(1, active / 3),
      activeRuns: active,
      queued: 0,
    });
  }
  const runs: FleetRun[] = running
    .filter((t) => bySlug.has(t.agentSlug))
    .map((t) => ({
      id: t.id,
      agentId: bySlug.get(t.agentSlug)!.id,
      label: t.id.slice(0, 8),
      state: "in_flight",
      startedAt: t.startedAt ? Date.parse(t.startedAt) : undefined,
    }));
  return { now, clusters: [...groups.values()], agents: fleet, events: [], runs };
}
