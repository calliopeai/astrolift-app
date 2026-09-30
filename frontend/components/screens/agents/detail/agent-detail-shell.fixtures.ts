import type {
  AstroliftAgentDetail,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentSkill,
  AstroliftToolDef,
} from "@/graphql/agents/agents.types";

import { agentFleetSnapshot } from "./agent-fleet-snapshot";
import type { AgentModelAccessViewProps } from "./AgentModelAccess";
import type { AgentOverviewProps, AgentOverviewTask } from "./use-agent-overview";

/**
 * Hand-typed fixtures for the agent detail group: the agent row the frame
 * and tabs read, the Overview tab and the Model access section. Records
 * carry only the fields these views read (plus the required facade fields).
 */

/** The generated JSON scalar is typed Record<string, unknown>; real values can be any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

export const LONG =
  "platform-team-shared-production-research-agent-with-a-deliberately-long-name-that-keeps-going";

const MINUTES_AGO = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

export const AGENT: AstroliftAgentListItem = {
  id: "wl-1",
  name: "Research Scout",
  slug: "research-scout",
  appSlug: "research-scout",
  projectSlug: "growth",
  sourceRepo: "acme/research-scout",
  sourceUrl: "https://github.com/acme/research-scout",
  runFamily: "task",
  runMode: "schedule",
  runPaused: false,
  runCronExpression: "0 * * * *",
  replicas: 1,
  runMaxParallel: null,
  scheduledScaleTo: null,
  scaleUpCron: "",
  scaleDownCron: "",
  lastRunStatus: "completed",
  lastRunAt: MINUTES_AGO(12),
  runningCount: 0,
};

export const RUNNING_AGENT: AstroliftAgentListItem = { ...AGENT, runningCount: 2 };

export const PAUSED_AGENT: AstroliftAgentListItem = {
  ...AGENT,
  runPaused: true,
  sourceUrl: "",
  lastRunAt: null,
};

export const LONG_AGENT: AstroliftAgentListItem = {
  ...AGENT,
  name: LONG,
  slug: LONG,
  appSlug: LONG,
  projectSlug: LONG,
  sourceRepo: `acme/${LONG}`,
  runFamily: "long_running_custom_family",
  runMode: "event_driven_custom_mode",
};

// ── Overview ────────────────────────────────────────────────────────────────

function tool(id: string, name: string): AstroliftToolDef {
  return {
    id,
    name,
    slug: id,
    description: `${name} tool`,
    adapter: "mcp",
    inputSchema: json({ type: "object" }),
    outputSchema: json({ type: "object" }),
    handlerRef: `tools/${id}`,
    createdAt: MINUTES_AGO(60 * 24 * 7),
  };
}

function skill(
  position: number,
  id: string,
  name: string,
  tools: AstroliftToolDef[]
): AstroliftAgentSkill {
  return {
    position,
    skill: {
      id,
      name,
      slug: id,
      description: `${name} skill`,
      content: "",
      skillVersion: 1,
      isGlobal: false,
      isActive: true,
      createdAt: MINUTES_AGO(60 * 24 * 7),
      updatedAt: MINUTES_AGO(60 * 24),
    },
    toolDefs: tools,
  };
}

export const DETAIL: AstroliftAgentDetail = {
  id: AGENT.id,
  name: AGENT.name,
  slug: AGENT.slug,
  appSlug: AGENT.appSlug,
  sourceRepo: AGENT.sourceRepo,
  runFamily: AGENT.runFamily,
  runMode: AGENT.runMode,
  runPaused: false,
  runCronExpression: AGENT.runCronExpression,
  imageRef: "ghcr.io/acme/research-scout:1.4.2",
  dockerfilePath: "Dockerfile",
  brief: {
    id: "brief-1",
    contentHash: "sha256:abc123",
    storageKey: "briefs/research-scout.md",
    config: json({}),
    createdAt: MINUTES_AGO(60 * 24),
  },
  skills: [
    skill(0, "web-research", "Web research", [tool("search", "Search"), tool("fetch", "Fetch")]),
    // "fetch" is shared across skills; the graph de-dupes it.
    skill(1, "summarise", "Summarise", [tool("fetch", "Fetch"), tool("write-note", "Write note")]),
  ],
};

function task(id: string, status: string, minutesAgo: number): AgentOverviewTask {
  return {
    id,
    status,
    createdAt: MINUTES_AGO(minutesAgo),
    startedAt: status === "queued" ? null : MINUTES_AGO(minutesAgo),
    finishedAt: status === "running" || status === "queued" ? null : MINUTES_AGO(minutesAgo - 1),
  };
}

export const TASKS: AgentOverviewTask[] = [
  task("7f3c9a10-1111-4a2b-9c3d-000000000001", "completed", 12),
  task("a91b2c33-2222-4a2b-9c3d-000000000002", "failed", 70),
  task("c0d4e5f6-3333-4a2b-9c3d-000000000003", "succeeded", 130),
  task("d1e2f3a4-4444-4a2b-9c3d-000000000004", "cancelled", 190),
  task("e5f6a7b8-5555-4a2b-9c3d-000000000005", "timed_out", 250),
  task("f9a0b1c2-6666-4a2b-9c3d-000000000006", "completed", 310),
];

export const RUNNING_TASKS: AgentOverviewTask[] = [
  task("0a1b2c3d-7777-4a2b-9c3d-000000000007", "running", 1),
  task("1b2c3d4e-8888-4a2b-9c3d-000000000008", "queued", 0),
  ...TASKS,
];

/** The org's fleet around the agent: two projects, one agent failing, one running. */
export const FLEET_AGENTS: AstroliftAgentListItem[] = [
  AGENT,
  { ...AGENT, id: "wl-2", name: "BDR Outreach", slug: "bdr-outreach", runningCount: 1 },
  { ...AGENT, id: "wl-3", name: "Cost Watch", slug: "cost-watch", lastRunStatus: "failed" },
  { ...AGENT, id: "wl-4", name: "Docs Sync", slug: "docs-sync", projectSlug: "platform" },
  { ...AGENT, id: "wl-5", name: "K8s Janitor", slug: "k8s-janitor", projectSlug: "" },
];

/** A fixed clock, so the fleet reads the same whenever the story runs. */
const FLEET_NOW = Date.UTC(2026, 8, 28, 14, 0, 0);

export const OVERVIEW: AgentOverviewProps = {
  agent: AGENT,
  detail: DETAIL,
  detailLoading: false,
  detailError: null,
  onRetryDetail: () => {},
  runs: { rows: TASKS.slice(0, 5), count: 48, loading: false, error: null, onRetry: () => {} },
  fleet: agentFleetSnapshot(FLEET_AGENTS, AGENT.id, TASKS.slice(0, 5), FLEET_NOW),
  onSelectAgent: () => {},
  sendingInput: false,
  onSendInput: async () => true,
};

// ── Settings: Model access ──────────────────────────────────────────────────

export const ENV_SPEC: AstroliftAgentEnvironmentSpec = {
  id: "spec-1",
  slug: AGENT.slug,
  name: AGENT.name,
  runtime: "python",
  imageTag: "3.12",
  agentType: "claude",
  vncEnabled: true,
  secretRefs: json([]),
  managedModel: true,
};

export const MODEL_ACCESS: AgentModelAccessViewProps = {
  spec: ENV_SPEC,
  loading: false,
  orgId: "org-1",
};
