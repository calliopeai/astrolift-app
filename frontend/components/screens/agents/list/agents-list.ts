/**
 * The Agents list declaration (spec 44 §5.1, §4.4), the list state spelled
 * as `agentFleetPage` variables, and the step that makes a server row a list
 * row.
 *
 * The server answers every view, chip, search, sort and page (#2155):
 * status, model, runtime, cluster and owner are columns on the fleet row, so
 * a numbered page's totalCount is exact. The only thing the hook adds is the
 * next firing of the scheduled agents on the page, from `agentUpcomingRuns`.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, formatSort, standardViews } from "@/components/list/list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import type { AstroliftAgentFleetRow, AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

/** What the agent is doing now: the fleet row's server-computed status. */
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

/**
 * How the agent reaches its model: the managed in-cluster model, the org's
 * model gateway, or its own API key.
 */
export type AgentModelKey = "managed" | "gateway" | "api-key";

export const AGENT_MODEL_LABEL: Record<AgentModelKey, string> = {
  managed: "Managed model",
  gateway: "Model gateway",
  "api-key": "API key",
};

/** One row: the fleet row as the screen reads it. */
export type AgentRow = AstroliftAgentListItem & {
  status: AgentStatusKey;
  running: number;
  /** The next firing of a scheduled agent, from `agentUpcomingRuns`. */
  nextScheduledAt: string | null;
  /** Null when the agent has no environment spec of its own. */
  model: AgentModelKey | null;
  /** The spec's runtime (claude, codex…), or null without one. */
  runtime: string | null;
  /** Clusters its app's environments deploy to, in environment order. */
  clusters: string[];
  /** The viewer registered it. */
  mine: boolean;
  /** Who registered it; empty for agents registered before owners were recorded. */
  ownerEmail: string;
};

const isStatus = (s: string): s is AgentStatusKey => (AGENT_STATUS_ORDER as string[]).includes(s);
const isModel = (s: string | null | undefined): s is AgentModelKey =>
  typeof s === "string" && s in AGENT_MODEL_LABEL;

/** A server fleet row as a list row; `next` is its next firing, when scheduled. */
export function toAgentRow(item: AstroliftAgentFleetRow, next: string | null = null): AgentRow {
  return {
    ...item,
    status: isStatus(item.status) ? item.status : "idle",
    running: item.runningCount,
    nextScheduledAt: next,
    model: isModel(item.modelSource) ? item.modelSource : null,
    runtime: item.runtime || null,
    clusters: item.clusterSlugs,
    mine: item.ownedByMe,
  };
}

/** The value Mine filters on: agents the viewer registered. */
export const MINE = "me";

export const AGENTS_LIST: ListDefinition = {
  id: "agents",
  fields: [
    // Free text: a project slug.
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
    // Free text: the spec's runtime or the run family (task, service).
    { key: "runtime", label: "Runtime" },
    // Free text: a cluster slug the agent's app deploys to.
    { key: "cluster", label: "Cluster" },
  ],
  // The server matches name, slug, app, project and source repo.
  searchPlaceholder: "Search agents, slugs, repos...",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews(
    { owner: MINE },
    [{ key: "paused", label: "Paused", filters: { paused: "1" } }],
    {
      mineNote:
        "Mine means agents you registered. Agents registered before Astrolift recorded who registered them have no owner and show only in All.",
    }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** The list filter keys `AstroliftAgentFleetFilter` takes as lists. */
const LIST_FILTER_KEYS = ["project", "status", "model", "runtime", "cluster", "owner"] as const;

export interface AgentFleetFilter {
  project?: string[];
  status?: string[];
  model?: string[];
  runtime?: string[];
  cluster?: string[];
  owner?: string[];
  paused?: boolean;
}

export interface AgentFleetPageVariables {
  orgId: string;
  search: string | null;
  filter: AgentFleetFilter | null;
  sort: string;
  page: number;
  pageSize: number;
}

/**
 * The list state as `agentFleetPage` variables. `sort` is always sent, which
 * selects numbered paging on the server; an empty filter is `null`.
 */
export function agentFleetPageVariables(
  orgId: string,
  {
    q,
    filters,
    sort,
    page,
    pageSize,
  }: {
    q: string;
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): AgentFleetPageVariables {
  const filter: AgentFleetFilter = {};
  for (const key of LIST_FILTER_KEYS) if (filters[key]) filter[key] = [filters[key]];
  if (filters.paused) filter.paused = true;
  return {
    orgId,
    search: q.trim() || null,
    filter: Object.keys(filter).length ? filter : null,
    sort: formatSort(sort.length ? sort : AGENTS_LIST.defaultSort),
    page: Math.max(1, page),
    pageSize,
  };
}

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
