/**
 * The combined run audit (spec 44 §4.4, decision 14): agent runs, workflow
 * runs, deployments, job runs and task runs in one list at Admin › Usage &
 * governance › Runs, with who or what started each, when, and the outcome.
 *
 * The server answers it (#2152): `astroliftRunAudit` reads every kind under
 * its own permission, normalises the outcome, records the initiator and
 * pages one cursor over all of them with an exact count. Pure: the list
 * declaration, the list state as the query's variables, and the one mapper
 * from the server's row into `CombinedRun`.
 */
import type { AstroliftRunAuditItem } from "@/graphql/__generated__/schema";

import type { CsvColumn } from "@/components/list/exportCsv";
import { type ListDefinition, standardViews } from "@/components/list/list-state";

import { SINCE_OPTIONS, sinceToIso } from "./audit-list";

export type RunKind = "agent" | "workflow" | "deployment" | "job" | "task";

/** The outcome, normalised by the server across the kinds' status words. */
export type RunOutcome = "running" | "waiting" | "succeeded" | "failed" | "cancelled" | "unknown";

export interface CombinedRun {
  /** `<kind>:<source id>`: unique across the kinds. */
  key: string;
  kind: RunKind;
  /** The source row's own id, as its detail page takes it. */
  id: string;
  /** What ran: the agent, the workflow, the app and environment, the job or task. */
  subject: string;
  /** The project or app it belongs to. */
  scope: string;
  /** Who started it: a person, else a deployment's commit author; empty when nobody did. */
  startedBy: string;
  /** How it started: manual, api, schedule, webhook, parent, and the source's own word. */
  trigger: string;
  /** True when the viewer started it. */
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
  task: "Task run",
};

const OUTCOMES: RunOutcome[] = ["running", "waiting", "succeeded", "failed", "cancelled"];
const TRIGGERS = ["manual", "api", "schedule", "webhook", "parent", "unknown"];

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
    { key: "trigger", label: "Trigger", options: TRIGGERS.map((t) => ({ value: t, label: t })) },
    // A user id, or `me`: the server matches the initiator exactly.
    { key: "startedBy", label: "Started by" },
    { key: "project", label: "Project" },
    { key: "app", label: "App" },
    { key: "since", label: "Since", options: SINCE_OPTIONS },
  ],
  // The server matches the run id prefix, status, subject, environment,
  // branch, commit author and the initiator's username.
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
 * A source status as an outcome, for the surfaces that read one kind's own
 * rows (the run audit gets the server's). Deployments speak their own
 * dialect: a `running` deployment is live, so it succeeded, and
 * `superseded` means it was live until the next one replaced it.
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

/** The `AstroliftRunAuditFilter` fields the list sends; unset ones are left out. */
export interface RunAuditFilter {
  kind?: string[];
  outcome?: string[];
  trigger?: string[];
  startedBy?: string[];
  project?: string[];
  app?: string[];
  since?: string;
}

export interface RunAuditVariables {
  filter: RunAuditFilter | null;
  search: string | null;
  sort: string;
  first: number;
  after: string | null;
}

/** The list keys that go to the filter as a one-value list. */
const LIST_KEYS = ["kind", "outcome", "trigger", "startedBy", "project", "app"] as const;

/**
 * The list state as `astroliftRunAudit` variables. `sort` is `-at` or `at`,
 * the only two orders the server takes; an empty filter is `null`.
 */
export function runAuditVariables(
  {
    filters,
    q,
    sort,
    pageSize,
    after,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: { key: string; dir: "asc" | "desc" }[];
    pageSize: number;
    after: string | null;
  },
  now: number
): RunAuditVariables {
  const filter: RunAuditFilter = {};
  for (const key of LIST_KEYS) if (filters[key]) filter[key] = [filters[key]];
  const since = sinceToIso(filters.since, now);
  if (since) filter.since = since;
  const at = sort.find((s) => s.key === "at");
  return {
    filter: Object.keys(filter).length ? filter : null,
    search: q.trim() || null,
    sort: at?.dir === "asc" ? "at" : "-at",
    first: pageSize,
    after,
  };
}

export type RunAuditItem = Pick<
  AstroliftRunAuditItem,
  | "kind"
  | "id"
  | "subject"
  | "scope"
  | "agentSlug"
  | "workflowSlug"
  | "trigger"
  | "sourceTrigger"
  | "startedByDisplay"
  | "startedByMe"
  | "at"
  | "durationSeconds"
  | "status"
  | "outcome"
>;

const KINDS = new Set<string>(Object.keys(RUN_KIND_LABEL));
const OUTCOME_SET = new Set<string>([...OUTCOMES, "unknown"]);

/** Where each kind's row opens: its own detail page. */
function hrefOf(r: RunAuditItem): string {
  const id = encodeURIComponent(r.id);
  switch (r.kind) {
    case "agent":
      return `/agents/runs/${id}`;
    case "workflow":
      return `/workflows/${encodeURIComponent(r.workflowSlug)}/runs/${id}`;
    case "deployment":
      return `/deployments/${id}`;
    case "job":
      return `/jobs/runs/${id}`;
    default:
      return `/tasks/runs/${id}`;
  }
}

/**
 * One server row as the list's row. The trigger reads as the normalised
 * word with the source's own beside it where they differ (`webhook · push`).
 */
export function fromRunAuditItem(r: RunAuditItem): CombinedRun {
  const kind = (KINDS.has(r.kind) ? r.kind : "task") as RunKind;
  const trigger =
    r.sourceTrigger && r.sourceTrigger !== r.trigger
      ? `${r.trigger} · ${r.sourceTrigger}`
      : r.trigger;
  return {
    key: `${kind}:${r.id}`,
    kind,
    id: r.id,
    subject: r.subject,
    scope: r.scope,
    startedBy: r.startedByDisplay,
    trigger: trigger === "unknown" ? "" : trigger,
    startedByMe: r.startedByMe,
    at: r.at,
    durationSeconds: r.durationSeconds ?? null,
    status: r.status,
    outcome: (OUTCOME_SET.has(r.outcome) ? r.outcome : "unknown") as RunOutcome,
    href: hrefOf({ ...r, kind }),
  };
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
