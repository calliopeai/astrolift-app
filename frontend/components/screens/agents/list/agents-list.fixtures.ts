import type { SortState } from "@/components/data-table";
import { fakeController } from "@/components/data-table/fixtures";
import type {
  AstroliftAgentBox,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentFleetRow,
} from "@/graphql/agents/agents.types";

import { AGENT_STATUS_ORDER, type AgentRow, toAgentRow } from "./agents-list";
import type { AgentsListScreenProps } from "./AgentsListScreen";
import type { BoxesTabViewProps } from "./BoxesTabView";
import type { ManagedModelSectionViewProps } from "./ManagedModelSectionView";
import type { VncSessionSectionViewProps } from "./VncSessionSectionView";

/** Hand-typed fixtures for the Agents list and the parts it handed on (group agents-list). */

const noop = () => {};
const resolvedTrue = async () => true;
const resolved = async () => {};

/** The JSON scalar is typed Record<string, unknown>; real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const LONG_NAME =
  "An extremely long agent name that keeps going to check wrapping in the registry table cell";

// ---------------------------------------------------------------------------
// Fleet rows, as `agentFleetPage` returns them
// ---------------------------------------------------------------------------

export function agent(
  slug: string,
  patch: Partial<AstroliftAgentFleetRow> = {}
): AstroliftAgentFleetRow {
  return {
    id: `wl-${slug}`,
    name: slug,
    slug,
    appSlug: "support",
    projectSlug: "platform",
    sourceRepo: "calliopeai/support-agents",
    sourceUrl: "https://github.com/calliopeai/support-agents",
    runFamily: "task",
    runMode: "schedule",
    runPaused: false,
    runCronExpression: "0 * * * *",
    lastRunStatus: "completed",
    lastRunAt: "2026-09-28T09:00:00Z",
    runningCount: 0,
    status: "idle",
    modelSource: null,
    runtime: "",
    environmentSpecSlug: "",
    clusterSlugs: [],
    ownerEmail: "",
    ownedByMe: false,
    ...patch,
  };
}

const SUPPORT_CLUSTERS = ["conflict-astrolift", "eks-us-west-2"];
const ME_EMAIL = "leo@example.com";

export const FLEET: AstroliftAgentFleetRow[] = [
  agent("triage-bot", {
    name: "Triage bot",
    runningCount: 2,
    lastRunStatus: "running",
    status: "running",
    modelSource: "managed",
    runtime: "claude",
    environmentSpecSlug: "triage-bot",
    clusterSlugs: SUPPORT_CLUSTERS,
    ownerEmail: ME_EMAIL,
    ownedByMe: true,
  }),
  agent("nightly-report", {
    name: "Nightly report",
    lastRunStatus: "failed",
    appSlug: "reports",
    status: "failing",
    modelSource: "api-key",
    runtime: "codex",
    environmentSpecSlug: "nightly-report",
    clusterSlugs: ["gke-eu-west-4"],
    ownerEmail: "ops@example.com",
  }),
  agent("hourly-digest", {
    name: "Hourly digest",
    status: "scheduled",
    modelSource: "gateway",
    runtime: "claude",
    environmentSpecSlug: "hourly-digest",
    clusterSlugs: SUPPORT_CLUSTERS,
    ownerEmail: "ops@example.com",
  }),
  agent("paused-sync", {
    name: "Paused sync",
    runPaused: true,
    runMode: "loop",
    status: "paused",
    modelSource: "api-key",
    runtime: "claude",
    environmentSpecSlug: "paused-sync",
    clusterSlugs: SUPPORT_CLUSTERS,
    ownerEmail: ME_EMAIL,
    ownedByMe: true,
  }),
  agent("docs-service", {
    name: "Docs service",
    projectSlug: "docs",
    appSlug: "docs",
    runFamily: "service",
    runMode: "service",
    sourceUrl: "",
    lastRunStatus: null,
    lastRunAt: null,
  }),
  agent("no-repo", {
    name: "No repo",
    sourceRepo: "",
    sourceUrl: "",
    runMode: "custom_mode",
    clusterSlugs: SUPPORT_CLUSTERS,
    ownerEmail: ME_EMAIL,
    ownedByMe: true,
  }),
];

/** The next firing `agentUpcomingRuns` reports for the scheduled agent. */
export const NEXT_DIGEST = "2026-09-28T23:00:00Z";

export const AGENT_ROWS: AgentRow[] = FLEET.map((a) =>
  toAgentRow(a, a.slug === "hourly-digest" ? NEXT_DIGEST : null)
);

const LONG_SLUG = "a-very-long-agent-slug-that-keeps-going-and-going-for-wrapping-0123456789abcdef";

export const LONG_AGENT_ROW: AgentRow = toAgentRow(
  agent(LONG_SLUG, {
    name: LONG_NAME,
    projectSlug: "a-very-long-project-slug-for-the-coordinates-line",
    appSlug:
      "arn:aws:ecs:us-west-2:123456789012:service/astrolift-agents/a-very-long-app-slug-for-the-coordinates-line-and-then-some-more-characters-to-pass-two-hundred",
    sourceRepo: "calliopeai/an-agent-repository-with-an-unreasonably-long-name-for-layout",
    sourceUrl:
      "https://github.com/calliopeai/an-agent-repository-with-an-unreasonably-long-name-for-layout/tree/main/agents/triage",
    lastRunStatus: "timed_out",
    status: "failing",
    modelSource: "api-key",
    runtime: "a-custom-runtime-with-a-long-name",
    environmentSpecSlug: LONG_SLUG,
    clusterSlugs: [
      "prd-us-west-2-tenant-shared-workloads-with-a-deliberately-long-slug",
      "eks-us-west-2",
    ],
    ownerEmail: "a.person.with.a.very.long.email.address@a-long-subdomain.example.com",
  })
);

/** 60 agents, for numbered pages. */
export const MANY_AGENT_ROWS: AgentRow[] = Array.from({ length: 60 }, (_, i) => ({
  ...AGENT_ROWS[i % AGENT_ROWS.length],
  id: `wl-agent-${i}`,
  slug: `agent-${String(i).padStart(2, "0")}`,
  name: `Agent ${String(i).padStart(2, "0")}`,
}));

const lower = (s: string | null | undefined) => (s ?? "").toLowerCase();

const SORT_VALUE: Record<string, (a: AgentRow) => string | number> = {
  name: (a) => a.name.toLowerCase(),
  status: (a) => AGENT_STATUS_ORDER.indexOf(a.status),
  lastRun: (a) => (a.lastRunAt ? Date.parse(a.lastRunAt) : 0),
  project: (a) => a.projectSlug.toLowerCase(),
};

/**
 * A stand-in for `agentFleetPage` in stories: the fixture rows filtered,
 * searched, sorted and sliced the way the server answers the list state.
 */
export function serveAgents(
  rows: AgentRow[],
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
): { rows: AgentRow[]; totalCount: number } {
  const needle = lower(q.trim());
  const kept = rows
    .filter(
      (a) =>
        (!filters.owner || a.mine) &&
        (!filters.paused || a.runPaused) &&
        (!filters.status || a.status === filters.status) &&
        (!filters.model || a.model === filters.model) &&
        (!filters.project || lower(a.projectSlug) === lower(filters.project)) &&
        (!filters.runtime ||
          lower(a.runtime) === lower(filters.runtime) ||
          lower(a.runFamily) === lower(filters.runtime)) &&
        (!filters.cluster || a.clusters.some((c) => lower(c) === lower(filters.cluster))) &&
        (!needle ||
          [a.name, a.slug, a.appSlug, a.projectSlug, a.sourceRepo].some((v) =>
            lower(v).includes(needle)
          ))
    )
    .sort((a, b) => {
      for (const s of sort) {
        const value = SORT_VALUE[s.key];
        if (!value) continue;
        const [x, y] = [value(a), value(b)];
        if (x !== y) return (x < y ? -1 : 1) * (s.dir === "asc" ? 1 : -1);
      }
      return a.slug < b.slug ? -1 : 1;
    });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
}

/** Everything the screen takes except `list`, which each story builds. */
export function listProps(
  patch: Partial<Omit<AgentsListScreenProps, "list">> = {}
): Omit<AgentsListScreenProps, "list"> {
  return {
    rows: AGENT_ROWS,
    totalCount: AGENT_ROWS.length,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    canCreate: true,
    canRun: true,
    dispatching: false,
    onRun: resolvedTrue,
    ...patch,
  };
}

// ---------------------------------------------------------------------------
// Boxes
// ---------------------------------------------------------------------------

export function box(slug: string, patch: Partial<AstroliftAgentBox> = {}): AstroliftAgentBox {
  return {
    id: `box-${slug}`,
    name: "Claude Dev box",
    slug,
    status: "running",
    agentSlug: "",
    environmentSpecSlug: "claude-dev",
    image: "agent-claude:1",
    idleTimeoutSeconds: 3600,
    sessionName: "astrolift",
    attachCommand: ["tmux", "new-session", "-A", "-s", "astrolift"],
    namespace: "astrolift-agents-acme",
    podName: `${slug}-0`,
    ownerEmail: "dev@example.com",
    lastError: "",
    startedAt: "2026-09-28T08:00:00Z",
    endedAt: null,
    lastAttachedAt: null,
    createdAt: "2026-09-28T08:00:00Z",
    ...patch,
  };
}

export const BOXES: AstroliftAgentBox[] = [
  box("box-claude-dev-abcd1234"),
  box("box-codex-5678efgh", {
    name: "Codex box",
    status: "provisioning",
    environmentSpecSlug: "codex",
    idleTimeoutSeconds: 0,
    startedAt: null,
  }),
  box("box-failed-0000", {
    name: "Failed box",
    status: "failed",
    idleTimeoutSeconds: 86400,
    lastError: "ImagePullBackOff: agent-claude:1 not found in registry",
  }),
  box("box-expired-9999", { name: "Expired box", status: "expired", idleTimeoutSeconds: 900 }),
];

export const LONG_BOXES: AstroliftAgentBox[] = [
  box("box-an-unreasonably-long-box-slug-that-keeps-going-for-layout-checks-0123456789", {
    name: LONG_NAME,
    lastError:
      "A very long error message from the provisioner that should clamp to two lines in the status cell rather than pushing the whole row taller than every other row in the table.",
  }),
];

export const SPECS: AstroliftAgentEnvironmentSpec[] = [
  {
    id: "s1",
    slug: "claude-dev",
    name: "Claude Dev",
    runtime: "claude",
    imageTag: "1",
    agentType: "claude",
    vncEnabled: false,
    secretRefs: json({ ANTHROPIC_API_KEY: "agents/org-1/anthropic" }),
    managedModel: false,
  },
  {
    id: "s2",
    slug: "codex",
    name: "Codex",
    runtime: "",
    imageTag: "1",
    agentType: "codex",
    vncEnabled: false,
    secretRefs: json([]),
    managedModel: false,
  },
];

export function boxesProps(
  rows: AstroliftAgentBox[] = BOXES,
  patch: Partial<BoxesTabViewProps> = {}
): BoxesTabViewProps {
  return {
    controller: fakeController<AstroliftAgentBox>({
      rows,
      totalCount: rows.length,
      state: rows.length ? "ready" : "empty",
      searchEnabled: false,
      sortEnabled: false,
    }),
    specs: SPECS,
    starting: false,
    includeEnded: false,
    toggleIncludeEnded: noop,
    startBox: resolvedTrue,
    destroyBox: resolved,
    copyAttach: noop,
    ...patch,
  };
}

// ---------------------------------------------------------------------------
// Spec toggles
// ---------------------------------------------------------------------------

export function vncProps(
  patch: Partial<VncSessionSectionViewProps> = {}
): VncSessionSectionViewProps {
  return { vncOn: false, vncBusy: false, onToggleVnc: resolved, ...patch };
}

export function managedModelProps(
  patch: Partial<ManagedModelSectionViewProps> = {}
): ManagedModelSectionViewProps {
  return { managedOn: false, managedBusy: false, onToggleManagedModel: resolved, ...patch };
}
