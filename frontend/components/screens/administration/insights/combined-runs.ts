/**
 * The combined run audit (spec 44 §4.4, decision 14): agent runs, workflow
 * runs, deployments and job runs in one list at Admin › Usage & governance ›
 * Runs, with who or what started each, when, and the outcome.
 *
 * Pure: the list declaration, one mapper per source into `CombinedRun`, and
 * the merge, filter and page steps the hook runs client-side until the
 * backend has one paged query over all four (see `useCombinedRuns`).
 */
import type { AstroliftAgentTask } from "@/graphql/agents/agents.types";
import type {
  AstroliftDeployment,
  AstroliftScheduledJobRun,
} from "@/graphql/lifecycle/lifecycle.types";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";

import type { CsvColumn } from "@/components/list/exportCsv";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";

import { SINCE_OPTIONS, sinceToIso } from "./audit-list";

export type RunKind = "agent" | "workflow" | "deployment" | "job";

/** The outcome, normalised across the four sources' status vocabularies. */
export type RunOutcome = "running" | "waiting" | "succeeded" | "failed" | "cancelled" | "unknown";

export interface CombinedRun {
  /** `<kind>:<source id>`: unique across the four sources. */
  key: string;
  kind: RunKind;
  /** The source row's own id, as its detail page takes it. */
  id: string;
  /** What ran: the agent, the workflow, the app and environment, the job. */
  subject: string;
  /** The project or app it belongs to, when the source says. */
  scope: string;
  /** Who started it: a person, a commit author; empty when the source does not say. */
  startedBy: string;
  /** How it started: manual, push, ci, schedule, api, a parent run. */
  trigger: string;
  /** True when the viewer started it, where the source can tell. */
  startedByMe: boolean;
  /** ISO time it started, or was created when it has not started yet. */
  at: string;
  durationSeconds: number | null;
  /** The source's own status word, shown beside the outcome. */
  status: string;
  outcome: RunOutcome;
  href: string;
}

export const RUN_KIND_LABEL: Record<RunKind, string> = {
  agent: "Agent run",
  workflow: "Workflow run",
  deployment: "Deployment",
  job: "Job run",
};

const OUTCOMES: RunOutcome[] = ["running", "waiting", "succeeded", "failed", "cancelled"];

export const RUN_AUDIT_LIST: ListDefinition = {
  id: "admin.runs",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: (Object.keys(RUN_KIND_LABEL) as RunKind[]).map((k) => ({
        value: k,
        label: RUN_KIND_LABEL[k],
      })),
    },
    { key: "outcome", label: "Outcome", options: OUTCOMES.map((o) => ({ value: o, label: o })) },
    { key: "startedBy", label: "Started by" },
    { key: "since", label: "Since", options: SINCE_OPTIONS },
  ],
  searchPlaceholder: "Search runs, agents, apps, ids…",
  defaultSort: [{ key: "at", dir: "desc" }],
  views: standardViews({ startedBy: "me" }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

// ---------------------------------------------------------------------------
// Outcomes
// ---------------------------------------------------------------------------

const GENERIC: Record<string, RunOutcome> = {
  running: "running",
  in_progress: "running",
  provisioning: "running",
  starting: "running",
  queued: "waiting",
  pending: "waiting",
  waiting: "waiting",
  paused: "waiting",
  awaiting_approval: "waiting",
  completed: "succeeded",
  succeeded: "succeeded",
  success: "succeeded",
  failed: "failed",
  error: "failed",
  timed_out: "failed",
  cancelled: "cancelled",
  canceled: "cancelled",
  aborted: "cancelled",
  terminated: "cancelled",
  superseded: "cancelled",
};

/**
 * A source status as an outcome. Deployments speak their own dialect: a
 * `running` deployment is live, so it succeeded, and `superseded` means it
 * was live until the next one replaced it.
 */
export function outcomeOf(kind: RunKind, status: string): RunOutcome {
  const s = status.toLowerCase();
  if (kind === "deployment") {
    if (s === "running" || s === "superseded") return "succeeded";
    if (s === "deploying" || s === "redeploying") return "running";
    if (s === "pending" || s === "pending_approval") return "waiting";
    if (s === "failed" || s === "rolled_back") return "failed";
  }
  return GENERIC[s] ?? "unknown";
}

// ---------------------------------------------------------------------------
// One mapper per source
// ---------------------------------------------------------------------------

export type AgentTaskSource = Pick<
  AstroliftAgentTask,
  | "id"
  | "agentSlug"
  | "agentName"
  | "projectSlug"
  | "status"
  | "createdAt"
  | "startedAt"
  | "finishedAt"
>;

function seconds(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null;
  const d = (Date.parse(to) - Date.parse(from)) / 1000;
  return Number.isFinite(d) && d >= 0 ? Math.round(d) : null;
}

/** An agent task. The source names no initiator, so Started by stays empty. */
export function fromAgentTask(t: AgentTaskSource): CombinedRun {
  return {
    key: `agent:${t.id}`,
    kind: "agent",
    id: t.id,
    subject: t.agentSlug || t.agentName,
    scope: t.projectSlug ?? "",
    startedBy: "",
    trigger: "",
    startedByMe: false,
    at: t.startedAt ?? t.createdAt,
    durationSeconds: seconds(t.startedAt, t.finishedAt),
    status: t.status,
    outcome: outcomeOf("agent", t.status),
    href: `/agents/runs/${encodeURIComponent(t.id)}`,
  };
}

/** A workflow run. A child run says so; the source names no person. */
export function fromWorkflowRun(r: WorkflowDefinitionRun): CombinedRun {
  return {
    key: `workflow:${r.guid}`,
    kind: "workflow",
    id: r.guid,
    subject: r.definitionName || r.definitionSlug,
    scope: r.projectSlug,
    startedBy: "",
    trigger: r.parentRunGuid ? "parent run" : "",
    startedByMe: false,
    at: r.startedAt ?? "",
    durationSeconds: seconds(r.startedAt, r.endedAt),
    status: r.status,
    outcome: outcomeOf("workflow", r.status),
    href: `/workflows/${encodeURIComponent(r.definitionSlug)}/observe`,
  };
}

export type DeploymentSource = Pick<
  AstroliftDeployment,
  | "id"
  | "registeredAppSlug"
  | "environmentName"
  | "workloadSlug"
  | "triggerKind"
  | "status"
  | "startedAt"
  | "createdAt"
  | "durationSeconds"
  | "commitAuthor"
  | "ciProvider"
  | "triggeredByUserId"
  | "triggeredByMe"
>;

/** A deployment: the commit author or the triggering user, and how it started. */
export function fromDeployment(d: DeploymentSource): CombinedRun {
  return {
    key: `deployment:${d.id}`,
    kind: "deployment",
    id: d.id,
    subject: `${d.registeredAppSlug} · ${d.environmentName}`,
    scope: d.registeredAppSlug,
    startedBy: d.commitAuthor || d.triggeredByUserId || "",
    trigger: d.ciProvider ? `${d.triggerKind} · ${d.ciProvider}` : d.triggerKind,
    startedByMe: d.triggeredByMe,
    at: d.startedAt ?? d.createdAt,
    durationSeconds: d.durationSeconds ?? null,
    status: d.status,
    outcome: outcomeOf("deployment", d.status),
    href: `/deployments/${encodeURIComponent(d.id)}`,
  };
}

export type JobRunSource = Pick<
  AstroliftScheduledJobRun,
  | "id"
  | "registeredAppSlug"
  | "environmentName"
  | "workloadSlug"
  | "status"
  | "startedAt"
  | "createdAt"
  | "durationSeconds"
>;

/** A scheduled job's run: its schedule started it. */
export function fromJobRun(j: JobRunSource): CombinedRun {
  return {
    key: `job:${j.id}`,
    kind: "job",
    id: j.id,
    subject: j.workloadSlug,
    scope: `${j.registeredAppSlug} · ${j.environmentName}`,
    startedBy: "",
    trigger: "schedule",
    startedByMe: false,
    at: j.startedAt ?? j.createdAt,
    durationSeconds: j.durationSeconds ?? null,
    status: j.status,
    outcome: outcomeOf("job", j.status),
    href: `/jobs/runs/${encodeURIComponent(j.id)}`,
  };
}

// ---------------------------------------------------------------------------
// Merge, filter, page (CLIENT-SIDE until the backend has the combined query)
// ---------------------------------------------------------------------------

/** Newest first; a run with no time sorts last. Ties break on the key, so the order is stable. */
export function mergeRuns(...sources: CombinedRun[][]): CombinedRun[] {
  return sources.flat().sort((a, b) => {
    const ta = a.at ? Date.parse(a.at) : -Infinity;
    const tb = b.at ? Date.parse(b.at) : -Infinity;
    return tb - ta || a.key.localeCompare(b.key);
  });
}

/**
 * The list's filters over merged rows. `startedBy: me` keeps only the runs
 * a source marks as the viewer's (deployments today); any other value
 * matches the Started by text. Search matches subject, scope, id, started
 * by and status, case-insensitive.
 */
export function filterRuns(
  runs: CombinedRun[],
  filters: Record<string, string>,
  q: string,
  now: number
): CombinedRun[] {
  const since = sinceToIso(filters.since, now);
  const sinceTs = since ? Date.parse(since) : null;
  const needle = q.trim().toLowerCase();
  const by = filters.startedBy?.toLowerCase();
  return runs.filter((r) => {
    if (filters.kind && r.kind !== filters.kind) return false;
    if (filters.outcome && r.outcome !== filters.outcome) return false;
    if (by === "me" ? !r.startedByMe : by && !r.startedBy.toLowerCase().includes(by)) return false;
    if (sinceTs !== null && (!r.at || Date.parse(r.at) < sinceTs)) return false;
    if (!needle) return true;
    return [r.subject, r.scope, r.id, r.startedBy, r.status, r.trigger].some((f) =>
      f.toLowerCase().includes(needle)
    );
  });
}

/**
 * One page of the filtered rows. The cursor is an offset (`o:50`), which
 * is only honest because the whole set is in memory; the backend query
 * will hand out real cursors.
 */
export function pageRuns(
  runs: CombinedRun[],
  after: string | null,
  pageSize: number
): { rows: CombinedRun[]; nextCursor: string | null } {
  const offset = after?.startsWith("o:") ? Math.max(0, Number(after.slice(2)) || 0) : 0;
  const end = offset + pageSize;
  return { rows: runs.slice(offset, end), nextCursor: end < runs.length ? `o:${end}` : null };
}

/** The CSV columns: what an auditor pastes into a spreadsheet. */
export const RUN_CSV: CsvColumn<CombinedRun>[] = [
  { header: "Kind", value: (r) => r.kind },
  { header: "Id", value: (r) => r.id },
  { header: "Subject", value: (r) => r.subject },
  { header: "Scope", value: (r) => r.scope },
  { header: "Started by", value: (r) => r.startedBy },
  { header: "Trigger", value: (r) => r.trigger },
  { header: "At", value: (r) => r.at },
  { header: "Duration (s)", value: (r) => r.durationSeconds },
  { header: "Status", value: (r) => r.status },
  { header: "Outcome", value: (r) => r.outcome },
];
