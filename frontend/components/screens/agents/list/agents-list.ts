/**
 * The Agents list declaration (spec 44 §5.1, §4.4) and the pure steps the
 * hook runs: the join that turns fleet rows into list rows, then views,
 * filters, sort and numbered pages.
 *
 * Why this runs here and not on the server: `agentFleet` is an unpaginated
 * list field with no filter, sort or page argument, and a fleet row carries
 * no model, runtime or cluster. The hook reads the org's fleet, live status,
 * environment specs (model and runtime; the spec slug is the agent slug) and
 * environments (cluster, by the agent's app) once each, `joinAgents` makes
 * rows of them, and `selectAgents` answers the list state. When the backend
 * grows the §5.1 contract the hook sends `list.filters`, `sort` and `page`
 * instead and this step goes away; the screen does not change.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import type {
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

/** What the agent is doing now, from its live status and last run. */
export type AgentStatusKey = "running" | "paused" | "failing" | "scheduled" | "idle";

export const AGENT_STATUS_ORDER: AgentStatusKey[] = [
  "running",
  "failing",
  "scheduled",
  "paused",
  "idle",
];

export const AGENT_STATUS_LABEL: Record<AgentStatusKey, string> = {
  running: "Running",
  paused: "Paused",
  failing: "Last run failed",
  scheduled: "Scheduled",
  idle: "Idle",
};

export const AGENT_STATUS_DOT: Record<
  AgentStatusKey,
  "ok" | "warn" | "error" | "muted" | "pending"
> = {
  running: "pending",
  paused: "muted",
  failing: "error",
  scheduled: "warn",
  idle: "ok",
};

/** How the agent reaches its model: the managed in-cluster model, or its own API key. */
export type AgentModelKey = "managed" | "api-key";

export const AGENT_MODEL_LABEL: Record<AgentModelKey, string> = {
  managed: "Managed model",
  "api-key": "API key",
};

/** One row: the fleet row, plus what the list learns from the other queries. */
export type AgentRow = AstroliftAgentListItem & {
  status: AgentStatusKey;
  /** Live running count, or the fleet row's until live status arrives. */
  running: number;
  nextScheduledAt: string | null;
  /** Null when the agent has no environment spec of its own. */
  model: AgentModelKey | null;
  /** The spec's runtime (claude, codex…), or null without one. */
  runtime: string | null;
  /** Clusters its app's environments deploy to, in environment order. */
  clusters: string[];
  /** On an app the viewer holds a role on (the Mine stand-in). */
  mine: boolean;
};

const FAILED_RUN = new Set(["failed", "timed_out", "error"]);

export function agentStatusKey(
  agent: Pick<AstroliftAgentListItem, "runningCount" | "runPaused" | "lastRunStatus">,
  live?: Pick<AstroliftAgentLiveStatus, "runningCount" | "isPaused" | "nextScheduledAt"> | null
): AgentStatusKey {
  if ((live?.runningCount ?? agent.runningCount) > 0) return "running";
  if (live?.isPaused ?? agent.runPaused) return "paused";
  if (agent.lastRunStatus && FAILED_RUN.has(agent.lastRunStatus.toLowerCase())) return "failing";
  if (live?.nextScheduledAt) return "scheduled";
  return "idle";
}

export const AGENTS_LIST: ListDefinition = {
  id: "agents",
  fields: [
    // Free text: matched on the project slug.
    { key: "project", label: "Project" },
    {
      key: "status",
      label: "Status",
      // Paused is its own view, not a status chip.
      options: AGENT_STATUS_ORDER.filter((s) => s !== "paused").map((value) => ({
        value,
        label: AGENT_STATUS_LABEL[value].toLowerCase(),
      })),
    },
    {
      key: "model",
      label: "Model",
      options: (Object.keys(AGENT_MODEL_LABEL) as AgentModelKey[]).map((value) => ({
        value,
        label: AGENT_MODEL_LABEL[value],
      })),
    },
    // Free text: matched on the spec's runtime or the run family (task, service).
    { key: "runtime", label: "Runtime" },
    // Free text: matched on a cluster slug the agent's app deploys to.
    { key: "cluster", label: "Cluster" },
  ],
  searchPlaceholder: "Search agents, slugs, repos...",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews(
    { mine: "1" },
    [{ key: "paused", label: "Paused", filters: { paused: "1" } }],
    {
      mineNote:
        "Mine means agents on apps you hold a role on, directly or through their project, team or organization, until agents record who owns them.",
    }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** `Agents ▾` [› tail]: the first crumb switches between the Agents area's functions. */
export function agentsCrumbs(active = "agents", tail: Crumb[] = []): Crumb[] {
  return [areaSwitcher(NAV, "agents", active), ...tail];
}

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

const RUN_FAMILY_LABELS: Record<string, string> = { task: "Task", service: "Service" };
const RUN_MODE_LABELS: Record<string, string> = {
  once: "Once",
  loop: "Loop",
  schedule: "Schedule",
  trigger: "Trigger",
  service: "Service",
};

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/**
 * runFamily + runMode as one label, "Task · Schedule" or "Service". Both are
 * free strings on the schema; an unknown value is title-cased, so a new
 * backend mode degrades to a readable word rather than a raw token.
 */
export function formatRunMode(runFamily: string, runMode: string): string {
  const family = RUN_FAMILY_LABELS[runFamily.toLowerCase()] ?? titleCase(runFamily);
  const mode = RUN_MODE_LABELS[runMode.toLowerCase()] ?? titleCase(runMode);
  if (!mode || mode === family) return family || "none";
  return `${family} · ${mode}`;
}

/** An agent run's status word (running, completed, timed_out…) as a status dot. */
export const RUN_STATUS_DOT: Record<string, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  queued: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

// ---------------------------------------------------------------------------
// Join
// ---------------------------------------------------------------------------

export interface AgentJoinSources {
  live: readonly AstroliftAgentLiveStatus[];
  specs: readonly Pick<AstroliftAgentEnvironmentSpec, "slug" | "runtime" | "managedModel">[];
  environments: readonly { registeredAppSlug: string; clusterSlug?: string | null }[];
  /** Slugs of the apps the viewer holds a role on; empty outside the Mine view. */
  myAppSlugs: ReadonlySet<string>;
}

/** Fleet rows plus live status, spec and environments, into list rows. */
export function joinAgents(
  agents: readonly AstroliftAgentListItem[],
  { live, specs, environments, myAppSlugs }: AgentJoinSources
): AgentRow[] {
  const liveById = new Map(live.map((l) => [l.workloadId, l]));
  const specBySlug = new Map(specs.map((s) => [s.slug, s]));
  const clustersByApp = new Map<string, string[]>();
  for (const e of environments) {
    if (!e.clusterSlug) continue;
    const seen = clustersByApp.get(e.registeredAppSlug) ?? [];
    if (!seen.includes(e.clusterSlug))
      clustersByApp.set(e.registeredAppSlug, [...seen, e.clusterSlug]);
  }
  return agents.map((a) => {
    const l = liveById.get(a.id) ?? null;
    const spec = specBySlug.get(a.slug);
    return {
      ...a,
      status: agentStatusKey(a, l),
      running: l?.runningCount ?? a.runningCount,
      nextScheduledAt: l?.nextScheduledAt ?? null,
      model: spec ? (spec.managedModel ? "managed" : "api-key") : null,
      runtime: spec?.runtime || null,
      clusters: clustersByApp.get(a.appSlug) ?? [],
      mine: myAppSlugs.has(a.appSlug),
    };
  });
}

// ---------------------------------------------------------------------------
// Select
// ---------------------------------------------------------------------------

type SortValue = string | number;

const time = (ts: string | null | undefined) => (ts ? Date.parse(ts) : 0);
const lower = (s: string | null | undefined) => (s ?? "").toLowerCase();

const SORT_VALUE: Record<string, (a: AgentRow) => SortValue> = {
  name: (a) => a.name.toLowerCase(),
  status: (a) => AGENT_STATUS_ORDER.indexOf(a.status),
  // Never run sorts before the oldest run.
  lastRun: (a) => time(a.lastRunAt),
  project: (a) => a.projectSlug.toLowerCase(),
};

function matches(a: AgentRow, filters: Record<string, string>): boolean {
  if (filters.mine && !a.mine) return false;
  if (filters.paused && a.status !== "paused") return false;
  if (filters.status && a.status !== lower(filters.status)) return false;
  if (filters.model && a.model !== filters.model) return false;
  if (filters.project && lower(a.projectSlug) !== lower(filters.project)) return false;
  if (filters.runtime) {
    const r = lower(filters.runtime);
    if (lower(a.runtime) !== r && lower(a.runFamily) !== r) return false;
  }
  if (filters.cluster && !a.clusters.some((c) => lower(c) === lower(filters.cluster))) return false;
  return true;
}

/** Client-side search until `agentFleet` takes one: name, slug, app, project, repo. */
function searched(a: AgentRow, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  return [a.name, a.slug, a.appSlug, a.projectSlug, a.sourceRepo].some((v) =>
    lower(v).includes(needle)
  );
}

function compare(a: AgentRow, b: AgentRow, sort: SortState[]): number {
  for (const s of sort) {
    const value = SORT_VALUE[s.key];
    if (!value) continue;
    const x = value(a);
    const y = value(b);
    if (x < y) return s.dir === "asc" ? -1 : 1;
    if (x > y) return s.dir === "asc" ? 1 : -1;
  }
  return a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0;
}

/**
 * One numbered page of the fleet: view filters and chips applied, searched,
 * sorted and sliced. `totalCount` is the filtered count, for "1–25 of 140".
 */
export function selectAgents(
  agents: readonly AgentRow[],
  {
    q = "",
    filters,
    sort,
    page,
    pageSize,
  }: {
    q?: string;
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): { rows: AgentRow[]; totalCount: number } {
  const kept = agents
    .filter((a) => matches(a, filters) && searched(a, q.trim()))
    .sort((a, b) => compare(a, b, sort));
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
}

// ---------------------------------------------------------------------------
// Former tabs
// ---------------------------------------------------------------------------

/**
 * Where each of the old fleet page's `?tab=` panels lives now (Leo's rule 3:
 * the Agents page is only the agents list). Registry is this list; the
 * Metrics, Logs and Zentinelle panels were placeholders with nothing behind
 * them, so they land here too.
 */
export const AGENTS_FORMER_TABS: Record<string, string> = {
  active: "/tasks?view=running",
  theatre: "/tasks?view=running",
  history: "/tasks",
  dispatch: "/agents/runs/new",
  boxes: "/workloads",
  registry: "/agents",
  metrics: "/agents",
  logs: "/agents",
  activity: "/agents",
  reasoning: "/agents",
  "token-usage": "/agents",
  compliance: "/agents",
};

/**
 * The redirect for an old `/agents?tab=…` link, or null when there is none.
 * A link that stays on this list keeps its other params (`?project=` is the
 * project filter here too); one that moves elsewhere drops them.
 */
export function agentsFormerTabTarget(
  searchParams: Record<string, string | string[] | undefined>
): string | null {
  const raw = searchParams.tab;
  if (raw === undefined) return null;
  const tab = Array.isArray(raw) ? raw[0] : raw;
  const target = (tab && AGENTS_FORMER_TABS[tab]) || "/agents";
  if (target !== "/agents") return target;
  const rest = new URLSearchParams();
  for (const [k, v] of Object.entries(searchParams)) {
    if (k === "tab" || v === undefined) continue;
    for (const one of Array.isArray(v) ? v : [v]) rest.append(k, one);
  }
  const qs = rest.toString();
  return qs ? `/agents?${qs}` : "/agents";
}
