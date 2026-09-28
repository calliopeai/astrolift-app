import type {
  AstroliftAgentDetail,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentSkill,
  AstroliftToolDef,
} from "@/graphql/agents/agents.types";

import type { AgentDetailShellViewProps } from "./AgentDetailShell";
import type { AgentModelAccessViewProps } from "./AgentModelAccess";
import type { AgentTabsViewProps } from "./AgentTabs";
import type { AppPlatformLinksViewProps } from "./AppPlatformLinks";
import type { AgentOverviewProps, AgentOverviewTask } from "./use-agent-overview";

/**
 * Hand-typed fixtures for the agent detail shell group: the shell header, the
 * pillar bar, the platform sub-page row, the Overview pillar and the settings
 * Model access card. Records carry only the fields these views read (plus the
 * required facade fields).
 */

/** The generated JSON scalar is typed Record<string, unknown>; real values can be any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const noop = async () => {};

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

// ── Shell ───────────────────────────────────────────────────────────────────

export const SHELL: AgentDetailShellViewProps = {
  agentSlug: AGENT.slug,
  agent: AGENT,
  loading: false,
  notFound: false,
};

// ── Pillar bar + platform row ───────────────────────────────────────────────

export const TABS: AgentTabsViewProps = {
  agentSlug: AGENT.slug,
  pathname: `/agents/${AGENT.slug}/overview`,
};

export const PLATFORM_LINKS: AppPlatformLinksViewProps = {
  agentSlug: AGENT.slug,
  pathname: `/agents/${AGENT.slug}/build`,
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

export const OVERVIEW: AgentOverviewProps = {
  agent: AGENT,
  detail: DETAIL,
  detailLoading: false,
  tasks: TASKS,
  dispatching: false,
  sendingInput: false,
  onDispatch: noop,
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
