/**
 * Agents › Runs (spec 44 §4.1, §4.4): every agent run and workflow run in
 * one list, plus the container task runs that used to be /tasks' own tabs,
 * so nothing that ran drops out of sight. An agent's Runs tab is the same
 * list, embedded and narrowed to that agent.
 *
 * Pure: the list declarations, one mapper per source into `RunRow`, and the
 * filter, sort and page steps the hook runs in the browser until the
 * backend has one paged runs query (see `useRuns`).
 */
import type { SortState } from "@/components/data-table";
import type { Crumb } from "@/components/shell/ShellHeader";
import {
  type ListDefinition,
  type ListView,
  standardViews,
} from "@/components/list/use-list-state";
import {
  outcomeOf,
  type RunOutcome,
} from "@/components/screens/administration/insights/combined-runs";
import { SINCE_OPTIONS, sinceToIso } from "@/components/screens/administration/insights/audit-list";
import type { AstroliftAgentTask } from "@/graphql/agents/agents.types";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

export type { RunOutcome };

export type RunKind = "agent" | "workflow" | "task";

export const RUN_KIND_LABEL: Record<RunKind, string> = {
  agent: "Agent run",
  workflow: "Workflow run",
  task: "Task run",
};

/** What a Cancel sends, per source; null once the run has finished. */
export type RunCancel =
  | { kind: "agent"; id: string }
  | { kind: "workflow"; workflowId: string }
  | null;

export interface RunRow {
  /** `<kind>:<source id>`: unique across the sources. */
  key: string;
  kind: RunKind;
  id: string;
  /** What ran: the agent, the workflow, the task workload. */
  subject: string;
  agent: string;
  workflow: string;
  project: string;
  /** The app a task run belongs to. */
  app: string;
  /** How it started: manual, api, schedule, workflow, parent run. Empty when not recorded. */
  trigger: string;
  startedBy: string;
  startedByMe: boolean;
  /** ISO time it started, or was created when it has not started yet. */
  at: string;
  durationSeconds: number | null;
  status: string;
  outcome: RunOutcome;
  href: string;
  cancel: RunCancel;
  /** The live session pop-out while a VNC-capable agent run is running. */
  watchHref: string | null;
}

/**
 * A run page's breadcrumb (spec 44 §4.4): `Agents ▾ › Runs`, then what ran,
 * then the run. At most four crumbs.
 */
export function runCrumbs(...rest: Crumb[]): Crumb[] {
  return [areaSwitcher(NAV, "agents", "runs"), { label: "Runs", href: "/tasks" }, ...rest];
}

// ---------------------------------------------------------------------------
// Declarations
// ---------------------------------------------------------------------------

const OUTCOMES: RunOutcome[] = ["running", "waiting", "succeeded", "failed", "cancelled"];

const TRIGGERS = ["manual", "api", "schedule", "workflow", "parent run"];

const STATUS_VIEWS: ListView[] = [
  { key: "running", label: "Running", filters: { status: "running" } },
  { key: "failed", label: "Failed", filters: { status: "failed" } },
  { key: "waiting", label: "Waiting", filters: { status: "waiting" } },
];

const common = {
  searchPlaceholder: "Search runs, agents, workflows, ids…",
  defaultSort: [{ key: "at", dir: "desc" }] as SortState[],
  paging: "cursor" as const,
  pageSizes: [25, 50, 100],
};

const statusField = {
  key: "status",
  label: "Status",
  options: OUTCOMES.map((o) => ({ value: o, label: o })),
};
const triggerField = {
  key: "trigger",
  label: "Trigger",
  options: TRIGGERS.map((t) => ({ value: t, label: t })),
};
const sinceField = { key: "since", label: "Since", options: SINCE_OPTIONS };
const startedByField = { key: "startedBy", label: "Started by" };

/** The Runs page: every kind, with the agent, workflow and project filters. */
export const RUNS_LIST: ListDefinition = {
  id: "agents.runs",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: (Object.keys(RUN_KIND_LABEL) as RunKind[]).map((k) => ({
        value: k,
        label: RUN_KIND_LABEL[k],
      })),
    },
    statusField,
    { key: "agent", label: "Agent" },
    { key: "workflow", label: "Workflow" },
    { key: "project", label: "Project" },
    triggerField,
    sinceField,
    startedByField,
  ],
  ...common,
  views: standardViews(
    { startedBy: "me" },
    [
      ...STATUS_VIEWS,
      {
        key: "scheduled",
        label: "Scheduled",
        filters: { trigger: "schedule" },
        note: "No run records a schedule trigger yet, so Scheduled stays empty until agent and workflow runs say how they started.",
      },
    ],
    {
      mineNote:
        "Mine covers task runs you started. Agent and workflow runs don't record who started them yet.",
    }
  ),
};

/** An agent's Runs tab: the same list, one agent, so no kind or agent filter. */
export const AGENT_RUNS_LIST: ListDefinition = {
  id: "agents.agent-runs",
  fields: [statusField, sinceField, startedByField],
  ...common,
  searchPlaceholder: "Search runs, ids…",
  views: standardViews({ startedBy: "me" }, STATUS_VIEWS, {
    mineNote: "Agent runs don't record who started them yet, so Mine stays empty until they do.",
  }),
};

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
  | "vncEnabled"
  | "vncUrl"
>;

function seconds(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null;
  const d = (Date.parse(to) - Date.parse(from)) / 1000;
  return Number.isFinite(d) && d >= 0 ? Math.round(d) : null;
}

const live = (o: RunOutcome) => o === "running" || o === "waiting";

export function fromAgentTask(t: AgentTaskSource): RunRow {
  const outcome = outcomeOf("agent", t.status);
  const id = encodeURIComponent(t.id);
  return {
    key: `agent:${t.id}`,
    kind: "agent",
    id: t.id,
    subject: t.agentSlug || t.agentName,
    agent: t.agentSlug,
    workflow: "",
    project: t.projectSlug ?? "",
    app: "",
    trigger: "",
    startedBy: "",
    startedByMe: false,
    at: t.startedAt ?? t.createdAt,
    durationSeconds: seconds(t.startedAt, t.finishedAt),
    status: t.status,
    outcome,
    href: `/agents/runs/${id}`,
    cancel: live(outcome) ? { kind: "agent", id: t.id } : null,
    watchHref: t.status === "running" && t.vncEnabled && t.vncUrl ? `/agents/runs/${id}/vnc` : null,
  };
}

export function fromWorkflowRun(r: WorkflowDefinitionRun): RunRow {
  const outcome = outcomeOf("workflow", r.status);
  return {
    key: `workflow:${r.guid}`,
    kind: "workflow",
    id: r.guid,
    subject: r.definitionName || r.definitionSlug,
    agent: "",
    workflow: r.definitionSlug,
    project: r.projectSlug,
    app: "",
    trigger: r.parentRunGuid ? "parent run" : "",
    startedBy: "",
    startedByMe: false,
    at: r.startedAt ?? "",
    durationSeconds: seconds(r.startedAt, r.endedAt),
    status: r.status,
    outcome,
    href: `/workflows/${encodeURIComponent(r.definitionSlug)}/runs/${encodeURIComponent(r.guid)}`,
    cancel:
      live(outcome) && r.temporalWorkflowId
        ? { kind: "workflow", workflowId: r.temporalWorkflowId }
        : null,
    watchHref: null,
  };
}

/** A container task run; `me` is the viewer's username, for Mine. */
export function fromTaskRun(r: AstroliftTaskRun, me: string | null): RunRow {
  return {
    key: `task:${r.id}`,
    kind: "task",
    id: r.id,
    subject: r.workloadSlug,
    agent: "",
    workflow: "",
    project: "",
    app: r.registeredAppSlug,
    trigger: r.triggerKind ?? "",
    startedBy: r.triggeredByUsername ?? "",
    startedByMe: Boolean(me) && r.triggeredByUsername === me,
    at: r.startedAt ?? r.createdAt,
    durationSeconds: r.durationSeconds ?? seconds(r.startedAt, r.endedAt),
    status: r.status,
    outcome: outcomeOf("job", r.status),
    href: `/tasks/runs/${encodeURIComponent(r.id)}`,
    cancel: null,
    watchHref: null,
  };
}

// ---------------------------------------------------------------------------
// Which sources a question needs
// ---------------------------------------------------------------------------

/**
 * A source is fetched only when the viewer may see its module and the
 * filters leave room for it: an agent chip rules out workflow and task
 * runs, a project chip rules out task runs (they carry an app, not a
 * project), and a kind chip keeps only its kind.
 */
export function activeSources(
  filters: Record<string, string>,
  canView: Record<RunKind, boolean>
): Record<RunKind, boolean> {
  const kind = filters.kind as RunKind | undefined;
  const wants = (k: RunKind) => canView[k] && (!kind || kind === k);
  return {
    agent: wants("agent") && !filters.workflow,
    workflow: wants("workflow") && !filters.agent,
    task: wants("task") && !filters.agent && !filters.workflow && !filters.project,
  };
}

// ---------------------------------------------------------------------------
// Filter, sort, page (CLIENT-SIDE until the backend has the runs query)
// ---------------------------------------------------------------------------

const has = (field: string, needle: string) => field.toLowerCase().includes(needle.toLowerCase());

/** The list's filters and search over the merged rows. */
export function filterRunRows(
  rows: RunRow[],
  filters: Record<string, string>,
  q: string,
  now: number
): RunRow[] {
  const since = sinceToIso(filters.since, now);
  const sinceTs = since ? Date.parse(since) : null;
  const needle = q.trim().toLowerCase();
  const by = filters.startedBy?.toLowerCase();
  return rows.filter((r) => {
    if (filters.kind && r.kind !== filters.kind) return false;
    if (filters.status && r.outcome !== filters.status) return false;
    if (filters.agent && !has(r.agent, filters.agent)) return false;
    if (filters.workflow && !has(r.workflow, filters.workflow)) return false;
    if (filters.project && !has(r.project, filters.project)) return false;
    if (filters.trigger && r.trigger.toLowerCase() !== filters.trigger.toLowerCase()) return false;
    if (by === "me" ? !r.startedByMe : by && !has(r.startedBy, by)) return false;
    if (sinceTs !== null && (!r.at || Date.parse(r.at) < sinceTs)) return false;
    if (!needle) return true;
    return [r.subject, r.agent, r.workflow, r.project, r.app, r.id, r.status, r.trigger].some((f) =>
      f.toLowerCase().includes(needle)
    );
  });
}

const time = (r: RunRow) => (r.at ? Date.parse(r.at) : -Infinity);

/**
 * Sort by the list's sort keys: `at` (Started) and `took`. A run with no
 * time or duration sorts last either way; ties break on the key.
 */
export function sortRunRows(rows: RunRow[], sort: SortState[]): RunRow[] {
  const keys = sort.length > 0 ? sort : [{ key: "at", dir: "desc" as const }];
  return [...rows].sort((a, b) => {
    for (const s of keys) {
      const va = s.key === "took" ? (a.durationSeconds ?? null) : time(a);
      const vb = s.key === "took" ? (b.durationSeconds ?? null) : time(b);
      const na = va === null || va === -Infinity;
      const nb = vb === null || vb === -Infinity;
      if (na !== nb) return na ? 1 : -1;
      if (!na && !nb && va !== vb) {
        const d = (va as number) - (vb as number);
        return s.dir === "asc" ? d : -d;
      }
    }
    return a.key.localeCompare(b.key);
  });
}

/**
 * One page of the sorted rows. The cursor is an offset (`o:50`), honest
 * only because the whole set is in memory; the backend query will hand out
 * real cursors.
 */
export function pageRunRows(
  rows: RunRow[],
  after: string | null,
  pageSize: number
): { rows: RunRow[]; nextCursor: string | null } {
  const offset = after?.startsWith("o:") ? Math.max(0, Number(after.slice(2)) || 0) : 0;
  const end = offset + pageSize;
  return { rows: rows.slice(offset, end), nextCursor: end < rows.length ? `o:${end}` : null };
}
