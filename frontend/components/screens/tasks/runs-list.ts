/**
 * Agents › Runs (spec 44 §4.1, §4.4): every agent run, workflow run and
 * container task run in one list, so nothing that ran drops out of sight.
 * An agent's Runs tab is the same list, embedded and narrowed to that agent.
 *
 * The server answers every view, chip, search, sort and page (#2152,
 * #2155): the Runs page reads `astroliftRunAudit` held to the three kinds
 * the Agents area owns, an agent's tab reads `agentTasksPage`, and the
 * Scheduled view reads the upcoming firings of scheduled agents from
 * `agentUpcomingRuns`. Pure: the declarations, the list state spelled as
 * each query's variables, and one mapper per source into `RunRow`.
 */
import type { SortState } from "@/components/data-table";
import type { Crumb } from "@/components/shell/ShellHeader";
import { type ListDefinition, type ListView, standardViews } from "@/components/list/list-state";
import { SINCE_OPTIONS, sinceToIso } from "@/components/screens/administration/insights/audit-list";
import type { AstroliftAgentTask, AstroliftAgentUpcomingRun } from "@/graphql/agents/agents.types";
import type {
  AstroliftAgentTask as GeneratedAgentTask,
  AstroliftRunAuditItem,
} from "@/graphql/__generated__/schema";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

export type RunKind = "agent" | "workflow" | "task";

/** The kinds the Agents area's Runs list holds, in the run audit's words. */
export const RUN_KINDS: RunKind[] = ["agent", "workflow", "task"];

export const RUN_KIND_LABEL: Record<RunKind, string> = {
  agent: "Agent run",
  workflow: "Workflow run",
  task: "Task run",
};

/** running, waiting, succeeded, failed, cancelled, unknown: each kind's status, normalised. */
export type RunOutcome = "running" | "waiting" | "succeeded" | "failed" | "cancelled" | "unknown";

/**
 * What a Cancel sends, per source; null once the run has finished. A
 * workflow run cancels by its Temporal id; a row that only knows the run's
 * guid (the run audit's) has it looked up at cancel time.
 */
export type RunCancel =
  | { kind: "agent"; id: string }
  | { kind: "workflow"; workflowId?: string; guid?: string }
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
  /** How it started: manual, api, schedule, webhook, parent. Empty when not recorded. */
  trigger: string;
  startedBy: string;
  startedByMe: boolean;
  /** ISO time it started, or was created when it has not started yet; the next firing when upcoming. */
  at: string;
  durationSeconds: number | null;
  status: string;
  outcome: RunOutcome;
  href: string;
  cancel: RunCancel;
  /** Retry runs a finished agent run again (`retryAgentTask`); absent otherwise. */
  retry?: { id: string } | null;
  /** The live session pop-out while a VNC-capable agent run is running. */
  watchHref: string | null;
  /** A scheduled agent's next firing (the Scheduled view), not a run that happened. */
  upcoming?: boolean;
}

/**
 * A run page's breadcrumb (spec 44 §4.4): `Agents ▾ › Runs`, then what ran,
 * then the run. At most four crumbs.
 */
export function runCrumbs(...rest: Crumb[]): Crumb[] {
  return [areaSwitcher(NAV, "agents", "runs"), { label: "Runs", href: "/tasks" }, ...rest];
}

/** An agent run page's crumbs: `Agents ▾ › Runs › <agent> › run a1b2c3d4`. */
export function agentRunCrumbs(
  task: Pick<AstroliftAgentTask, "agentSlug" | "agentName"> | null,
  last: Crumb
): Crumb[] {
  if (!task?.agentSlug) return runCrumbs(last);
  return runCrumbs(
    {
      label: task.agentName || task.agentSlug,
      href: `/agents/${encodeURIComponent(task.agentSlug)}`,
    },
    last
  );
}

// ---------------------------------------------------------------------------
// Outcomes
// ---------------------------------------------------------------------------

const OUTCOMES: RunOutcome[] = ["running", "waiting", "succeeded", "failed", "cancelled"];

/** An agent task's or task run's own status word as an outcome. */
const OUTCOME_OF: Record<string, RunOutcome> = {
  running: "running",
  draft: "waiting",
  queued: "waiting",
  pending: "waiting",
  provisioning: "waiting",
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
};

export function statusOutcome(status: string): RunOutcome {
  return OUTCOME_OF[status.toLowerCase()] ?? "unknown";
}

/** The agent task statuses behind each outcome, for `agentTasksPage`'s status list. */
const AGENT_STATUSES: Record<RunOutcome, string[]> = {
  running: ["running"],
  waiting: ["draft", "queued", "provisioning"],
  succeeded: ["completed"],
  failed: ["failed", "timed_out"],
  cancelled: ["cancelled"],
  unknown: [],
};

const live = (o: RunOutcome) => o === "running" || o === "waiting";
const finished = (o: RunOutcome) => o === "succeeded" || o === "failed" || o === "cancelled";

// ---------------------------------------------------------------------------
// Declarations
// ---------------------------------------------------------------------------

const TRIGGERS = ["manual", "api", "schedule", "webhook", "parent"];

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

/** The Scheduled view's filter: upcoming firings, not runs. */
export const UPCOMING = "upcoming";

/** The Runs page: every kind, with the agent, workflow and project filters. */
export const RUNS_LIST: ListDefinition = {
  id: "agents.runs",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: RUN_KINDS.map((k) => ({ value: k, label: RUN_KIND_LABEL[k] })),
    },
    statusField,
    // Free text: an agent slug.
    { key: "agent", label: "Agent" },
    // Free text: a workflow definition slug.
    { key: "workflow", label: "Workflow" },
    // Free text: a project slug.
    { key: "project", label: "Project" },
    triggerField,
    sinceField,
  ],
  ...common,
  views: standardViews(
    { startedBy: "me" },
    [
      ...STATUS_VIEWS,
      {
        key: "scheduled",
        label: "Scheduled",
        filters: { [UPCOMING]: "1" },
        note: "The next firings of scheduled agents, soonest first. Runs a schedule already started are in All, with the schedule trigger.",
      },
    ],
    {
      mineNote:
        "Mine means runs you started. Runs from before Astrolift recorded who started them show only in All.",
    }
  ),
};

/** An agent's Runs tab: the same list, one agent, so no kind or agent filter. */
export const AGENT_RUNS_LIST: ListDefinition = {
  id: "agents.agent-runs",
  fields: [statusField, triggerField],
  ...common,
  searchPlaceholder: "Search runs, ids…",
  views: standardViews({ startedBy: "me" }, STATUS_VIEWS, {
    mineNote:
      "Mine means runs of this agent you started. Runs from before Astrolift recorded who started them show only in All.",
  }),
};

// ---------------------------------------------------------------------------
// The list state as each query's variables
// ---------------------------------------------------------------------------

/** One direction on the one key both run queries sort by. */
const descending = (sort: SortState[]) => (sort[0]?.dir ?? "desc") === "desc";

export interface RunAuditFilter {
  kind: string[];
  outcome?: string[];
  agent?: string[];
  workflow?: string[];
  project?: string[];
  trigger?: string[];
  startedBy?: string[];
  since?: string;
}

/**
 * The Runs page's list state as `astroliftRunAudit` variables. `kinds` is
 * what the viewer's modules show; a Kind chip narrows it, and a chip for a
 * kind the viewer cannot see leaves nothing to ask (`null`).
 */
export function runAuditVariables(
  {
    filters,
    q,
    sort,
    after,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    after: string | null;
    pageSize: number;
  },
  kinds: RunKind[],
  now: number
): {
  filter: RunAuditFilter;
  search: string | null;
  sort: string;
  first: number;
  after: string | null;
} | null {
  const kind = filters.kind ? kinds.filter((k) => k === filters.kind) : kinds;
  if (kind.length === 0) return null;
  const filter: RunAuditFilter = { kind };
  if (filters.status) filter.outcome = [filters.status];
  for (const key of ["agent", "workflow", "project", "trigger", "startedBy"] as const) {
    if (filters[key]) filter[key] = [filters[key]];
  }
  const since = sinceToIso(filters.since, now);
  if (since) filter.since = since;
  return {
    filter,
    search: q.trim() || null,
    sort: descending(sort) ? "-at" : "at",
    first: pageSize,
    after,
  };
}

export interface AgentTasksFilter {
  status?: string[];
  trigger?: string[];
  startedByMe?: boolean;
}

/** An agent's Runs tab's list state as `agentTasksPage` variables, newest created first. */
export function agentTasksVariables(
  orgId: string,
  workloadId: string | null,
  {
    filters,
    q,
    sort,
    after,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    after: string | null;
    pageSize: number;
  }
) {
  const filter: AgentTasksFilter = {};
  if (filters.status) filter.status = AGENT_STATUSES[filters.status as RunOutcome] ?? [];
  if (filters.trigger) filter.trigger = [filters.trigger];
  if (filters.startedBy === "me") filter.startedByMe = true;
  return {
    orgId,
    workloadId,
    search: q.trim() || null,
    filter: Object.keys(filter).length ? filter : null,
    // The tab's Started column: the server orders agent tasks by when they were created.
    sort: descending(sort) ? "-created" : "created",
    limit: pageSize,
    after,
  };
}

/**
 * The Scheduled view's list state as `agentUpcomingRuns` variables. The
 * firings are numbered pages on the server; the list is a cursor list, so
 * the cursor is the next page number (`p:2`). A chip that only a past run
 * can match (a workflow, a task, a trigger other than schedule, an outcome
 * other than waiting) leaves nothing to ask (`null`).
 */
export function upcomingVariables(
  orgId: string,
  {
    filters,
    q,
    after,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    after: string | null;
    pageSize: number;
  }
) {
  if (filters.kind && filters.kind !== "agent") return null;
  if (filters.workflow) return null;
  if (filters.trigger && filters.trigger !== "schedule") return null;
  if (filters.status && filters.status !== "waiting") return null;
  const page = after?.startsWith("p:") ? Math.max(1, Number(after.slice(2)) || 1) : 1;
  return {
    orgId,
    search: q.trim() || null,
    agent: filters.agent ? [filters.agent] : null,
    project: filters.project ? [filters.project] : null,
    perAgent: 3,
    page,
    pageSize,
  };
}

/** The cursor for the Scheduled view's next page, or null on the last. */
export function upcomingNextCursor(page: number, pageSize: number, total: number): string | null {
  return page * pageSize < total ? `p:${page + 1}` : null;
}

// ---------------------------------------------------------------------------
// One mapper per source
// ---------------------------------------------------------------------------

export type RunAuditSource = Pick<
  AstroliftRunAuditItem,
  | "kind"
  | "id"
  | "subject"
  | "agentSlug"
  | "workflowSlug"
  | "projectSlug"
  | "appSlug"
  | "trigger"
  | "startedByDisplay"
  | "startedByMe"
  | "at"
  | "startedAt"
  | "endedAt"
  | "durationSeconds"
  | "status"
  | "outcome"
>;

/** An agent task as its Runs tab reads it; how it started is absent on older reads. */
export type AgentTaskSource = Pick<
  GeneratedAgentTask,
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
> &
  Partial<Pick<GeneratedAgentTask, "triggerKind" | "triggeredByMe">>;

function seconds(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null;
  const d = (Date.parse(to) - Date.parse(from)) / 1000;
  return Number.isFinite(d) && d >= 0 ? Math.round(d) : null;
}

const asOutcome = (o: string): RunOutcome =>
  (OUTCOMES as string[]).includes(o) ? (o as RunOutcome) : "unknown";

const recorded = (trigger: string) => (trigger === "unknown" ? "" : trigger);

function runHref(kind: RunKind, id: string, workflowSlug: string): string {
  const run = encodeURIComponent(id);
  if (kind === "agent") return `/agents/runs/${run}`;
  if (kind === "workflow") return `/workflows/${encodeURIComponent(workflowSlug)}/runs/${run}`;
  return `/tasks/runs/${run}`;
}

/**
 * A run audit row. `vnc` is the live session of a running agent run, when
 * the page read it (the audit row carries no VNC coordinates).
 */
export function fromRunAuditItem(
  r: RunAuditSource,
  vnc?: Pick<AstroliftAgentTask, "vncEnabled" | "vncUrl"> | null
): RunRow {
  const kind = (RUN_KINDS as string[]).includes(r.kind) ? (r.kind as RunKind) : "task";
  const outcome = asOutcome(r.outcome);
  return {
    key: `${kind}:${r.id}`,
    kind,
    id: r.id,
    subject: r.subject,
    agent: r.agentSlug,
    workflow: r.workflowSlug,
    project: r.projectSlug,
    app: r.appSlug,
    trigger: recorded(r.trigger),
    startedBy: r.startedByDisplay,
    startedByMe: r.startedByMe,
    at: r.at,
    durationSeconds: r.durationSeconds ?? seconds(r.startedAt, r.endedAt),
    status: r.status,
    outcome,
    href: runHref(kind, r.id, r.workflowSlug),
    cancel: !live(outcome)
      ? null
      : kind === "agent"
        ? { kind: "agent", id: r.id }
        : kind === "workflow"
          ? { kind: "workflow", guid: r.id }
          : null,
    retry: kind === "agent" && finished(outcome) ? { id: r.id } : null,
    watchHref:
      kind === "agent" && r.status === "running" && vnc?.vncEnabled && vnc.vncUrl
        ? `/agents/runs/${encodeURIComponent(r.id)}/vnc`
        : null,
    upcoming: false,
  };
}

/** One of an agent's own tasks, on its Runs tab. */
export function fromAgentTask(t: AgentTaskSource): RunRow {
  const outcome = statusOutcome(t.status);
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
    trigger: recorded(t.triggerKind ?? ""),
    startedBy: t.triggeredByMe ? "you" : "",
    startedByMe: t.triggeredByMe ?? false,
    at: t.startedAt ?? t.createdAt,
    durationSeconds: seconds(t.startedAt, t.finishedAt),
    status: t.status,
    outcome,
    href: `/agents/runs/${id}`,
    cancel: live(outcome) ? { kind: "agent", id: t.id } : null,
    retry: finished(outcome) ? { id: t.id } : null,
    watchHref: t.status === "running" && t.vncEnabled && t.vncUrl ? `/agents/runs/${id}/vnc` : null,
    upcoming: false,
  };
}

/** A scheduled agent's next firing, for the Scheduled view. */
export function fromUpcomingRun(u: AstroliftAgentUpcomingRun): RunRow {
  return {
    key: `upcoming:${u.agentId}:${u.scheduledAt}`,
    kind: "agent",
    id: u.cronExpression,
    subject: u.agentSlug || u.agentName,
    agent: u.agentSlug,
    workflow: "",
    project: u.projectSlug,
    app: u.appSlug,
    trigger: "schedule",
    startedBy: "",
    startedByMe: false,
    at: u.scheduledAt,
    durationSeconds: null,
    status: "scheduled",
    outcome: "waiting",
    href: `/agents/${encodeURIComponent(u.agentSlug)}`,
    cancel: null,
    retry: null,
    watchHref: null,
    upcoming: true,
  };
}

/** A container task run's page (TaskRunDetail); `me` is the viewer's username. */
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
    outcome: statusOutcome(r.status),
    href: `/tasks/runs/${encodeURIComponent(r.id)}`,
    cancel: null,
    retry: null,
    watchHref: null,
    upcoming: false,
  };
}

// ---------------------------------------------------------------------------
// A workflow's Runs tab (use-workflow-runs-tab) still reads a window of a
// definition's runs and filters, sorts and pages it in the browser. These
// are its steps until it reads `workflowDefinitionRunsPage`; nothing in the
// Agents area's own Runs list uses them.
// ---------------------------------------------------------------------------

export function fromWorkflowRun(r: WorkflowDefinitionRun): RunRow {
  const outcome = statusOutcome(r.status);
  return {
    key: `workflow:${r.guid}`,
    kind: "workflow",
    id: r.guid,
    subject: r.definitionName || r.definitionSlug,
    agent: "",
    workflow: r.definitionSlug,
    project: r.projectSlug,
    app: "",
    trigger: r.parentRunGuid ? "parent" : "",
    startedBy: "",
    startedByMe: false,
    at: r.startedAt ?? "",
    durationSeconds: seconds(r.startedAt, r.endedAt),
    status: r.status,
    outcome,
    href: runHref("workflow", r.guid, r.definitionSlug),
    cancel:
      live(outcome) && r.temporalWorkflowId
        ? { kind: "workflow", workflowId: r.temporalWorkflowId }
        : null,
    watchHref: null,
  };
}

const has = (field: string, needle: string) => field.toLowerCase().includes(needle.toLowerCase());

/** The list's filters and search over rows held in memory. */
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
 * Sort rows held in memory by `at` (Started) or `took`. A run with no time
 * or duration sorts last either way; ties break on the key.
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

/** One page of rows held in memory; the cursor is an offset (`o:50`). */
export function pageRunRows(
  rows: RunRow[],
  after: string | null,
  pageSize: number
): { rows: RunRow[]; nextCursor: string | null } {
  const offset = after?.startsWith("o:") ? Math.max(0, Number(after.slice(2)) || 0) : 0;
  const end = offset + pageSize;
  return { rows: rows.slice(offset, end), nextCursor: end < rows.length ? `o:${end}` : null };
}
