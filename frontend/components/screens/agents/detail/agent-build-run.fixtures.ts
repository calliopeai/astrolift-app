import type { AstroliftAgentSkill, AstroliftToolDef } from "@/graphql/agents/agents.types";

import type { AgentBuildScreenProps } from "./AgentBuildScreen";
import type { AgentRunScreenProps } from "./AgentRunScreen";
import type { AgentTask } from "./use-agent-run";

/** Hand-typed fixtures for the agent Build and Run tabs (group agent-build-run). */

/** The JSON scalar is typed as an object, but real values can be any JSON. */
const json = (v: unknown) => v as Record<string, unknown>;

const MINUTE = 60 * 1000;
/** Relative so "x minutes ago" reads the same whenever the story runs. */
const ago = (minutes: number) => new Date(Date.now() - minutes * MINUTE).toISOString();

export const LONG =
  "customer-facing-outbound-prospecting-agent-for-the-emea-enterprise-segment-with-a-deliberately-long-name";

// ---------------------------------------------------------------- Build

const TOOL_CRM: AstroliftToolDef = {
  id: "tool-crm",
  name: "Search CRM accounts",
  slug: "search-crm-accounts",
  description: "Find accounts in the CRM by segment, owner or last activity.",
  adapter: "http_endpoint",
  inputSchema: json({ type: "object", properties: { segment: { type: "string" } } }),
  outputSchema: json({ type: "array" }),
  handlerRef: "https://crm.internal.example.com/api/accounts/search",
  createdAt: "2026-09-01T12:00:00Z",
};

const TOOL_SCORE: AstroliftToolDef = {
  ...TOOL_CRM,
  id: "tool-score",
  name: "Score account",
  slug: "score-account",
  description: "",
  adapter: "python_fn",
  handlerRef: "agents.bdr.scoring:score_account",
};

const TOOL_MCP: AstroliftToolDef = {
  ...TOOL_CRM,
  id: "tool-mcp",
  name: "Draft outreach",
  slug: "draft-outreach",
  description: "Draft a first-touch email through the outreach MCP server.",
  adapter: "mcp_server",
  handlerRef: "",
};

const SKILL_PROSPECTING: AstroliftAgentSkill = {
  position: 0,
  skill: {
    id: "skill-prospecting",
    name: "Prospecting",
    slug: "prospecting",
    description: "Finds and scores accounts that match the ideal customer profile.",
    content: "",
    skillVersion: 3,
    isGlobal: true,
    isActive: true,
    createdAt: "2026-08-01T12:00:00Z",
    updatedAt: "2026-09-20T12:00:00Z",
  },
  toolDefs: [TOOL_CRM, TOOL_SCORE],
};

const SKILL_OUTREACH: AstroliftAgentSkill = {
  position: 1,
  skill: {
    ...SKILL_PROSPECTING.skill,
    id: "skill-outreach",
    name: "Outreach",
    slug: "outreach",
    description: "",
    skillVersion: 1,
    isGlobal: false,
    isActive: false,
  },
  toolDefs: [TOOL_MCP],
};

const SKILL_NO_TOOLS: AstroliftAgentSkill = {
  position: 2,
  skill: {
    ...SKILL_PROSPECTING.skill,
    id: "skill-notes",
    name: "Meeting notes",
    slug: "meeting-notes",
    description: "Summarises call transcripts into CRM notes.",
    isGlobal: false,
  },
  toolDefs: [],
};

export const BUILD: AgentBuildScreenProps = {
  agent: {
    slug: "bdr-outreach",
    appSlug: "sales-agents",
    projectSlug: "gtm",
    sourceRepo: "calliopeai/sales-agents",
    sourceUrl: "https://github.com/calliopeai/sales-agents",
  },
  workload: { kind: "job" },
  workloadLoading: false,
  primary: {
    name: "agent",
    isPrimary: true,
    imageRef: "ghcr.io/calliopeai/sales-agents/bdr-outreach:0.1.37",
    dockerfilePath: "agents/bdr/Dockerfile",
    buildContext: "agents/bdr",
  },
  containersLoading: false,
  detailLoading: false,
  detailError: false,
  brief: {
    id: "brief-1",
    contentHash: "sha256:9f2c4e1a7b3d5f60",
    storageKey: "briefs/bdr-outreach/9f2c4e1a.json",
    config: json({
      model: "claude-sonnet",
      temperature: 0.2,
      skills: ["prospecting", "outreach", "meeting-notes"],
      approvalGate: { required: true, approvers: ["sales-leads"] },
    }),
    createdAt: ago(90),
  },
  skills: [SKILL_PROSPECTING, SKILL_OUTREACH, SKILL_NO_TOOLS],
};

export const BUILD_LOADING: AgentBuildScreenProps = {
  ...BUILD,
  workload: null,
  workloadLoading: true,
  primary: null,
  containersLoading: true,
  detailLoading: true,
  brief: null,
  skills: [],
};

/** A freshly registered agent: no repo, container, brief or skills yet. */
export const BUILD_EMPTY: AgentBuildScreenProps = {
  ...BUILD,
  agent: { ...BUILD.agent, sourceRepo: "", sourceUrl: "" },
  workload: null,
  primary: null,
  brief: null,
  skills: [],
};

/** The `agent(slug)` join failed; source, image and manifest still render. */
export const BUILD_ERROR: AgentBuildScreenProps = {
  ...BUILD,
  detailError: true,
  brief: null,
  skills: [],
};

export const BUILD_LONG: AgentBuildScreenProps = {
  ...BUILD,
  agent: {
    slug: LONG,
    appSlug: `${LONG}-app`,
    projectSlug: `${LONG}-project`,
    sourceRepo: `calliopeai/${LONG}`,
    sourceUrl: `https://github.com/calliopeai/${LONG}`,
  },
  primary: {
    name: `${LONG}-container`,
    isPrimary: false,
    imageRef: `ghcr.io/calliopeai/${LONG}/${LONG}:0.1.37-rc.1-with-a-long-build-suffix`,
    dockerfilePath: `agents/${LONG}/docker/Dockerfile.production`,
    buildContext: `agents/${LONG}`,
  },
  brief: {
    ...BUILD.brief!,
    contentHash: `sha256:${"9f2c4e1a7b3d5f60".repeat(4)}`,
    config: json({ systemPrompt: `${LONG} `.repeat(6).trim(), skills: [LONG] }),
  },
  skills: [
    {
      ...SKILL_PROSPECTING,
      skill: {
        ...SKILL_PROSPECTING.skill,
        name: LONG,
        slug: LONG,
        description: `${LONG} `.repeat(4).trim(),
      },
      toolDefs: [
        {
          ...TOOL_CRM,
          name: LONG,
          description: `${LONG} `.repeat(3).trim(),
          adapter: "grpc_endpoint_from_a_future_adapter",
          handlerRef: `agents.${LONG}.handlers:run`,
        },
      ],
    },
  ],
};

// ---------------------------------------------------------------- Run

const TASK: AgentTask = {
  id: "7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b",
  status: "completed",
  callbackUrl: "",
  result: null,
  createdAt: ago(60),
  startedAt: ago(60),
  finishedAt: ago(55),
  vncEnabled: false,
  vncUrl: "",
  snapshotUrl: null,
};

export const RUN_ROWS: AgentTask[] = [
  {
    ...TASK,
    id: "a1b2c3d4-0000-4000-8000-000000000001",
    status: "running",
    createdAt: ago(2),
    startedAt: ago(2),
    finishedAt: null,
    vncEnabled: true,
    vncUrl: "/relay/vnc/a1b2c3d4",
  },
  {
    ...TASK,
    id: "a1b2c3d4-0000-4000-8000-000000000002",
    status: "running",
    createdAt: ago(4),
    startedAt: ago(4),
    finishedAt: null,
  },
  {
    ...TASK,
    id: "a1b2c3d4-0000-4000-8000-000000000003",
    status: "queued",
    createdAt: ago(1),
    startedAt: null,
    finishedAt: null,
  },
  { ...TASK, id: "a1b2c3d4-0000-4000-8000-000000000004" },
  {
    ...TASK,
    id: "a1b2c3d4-0000-4000-8000-000000000005",
    status: "failed",
    createdAt: ago(120),
    startedAt: ago(120),
    finishedAt: ago(118),
  },
  {
    ...TASK,
    id: "a1b2c3d4-0000-4000-8000-000000000006",
    status: "timed_out",
    createdAt: ago(240),
    startedAt: ago(240),
    finishedAt: ago(180),
  },
  {
    ...TASK,
    id: "a1b2c3d4-0000-4000-8000-000000000007",
    status: "cancelled",
    createdAt: ago(300),
    startedAt: ago(300),
    finishedAt: ago(299),
  },
];

/** The Runs screen's data, less the list controller a story makes with useLocalListState. */
export type RunData = Omit<AgentRunScreenProps, "renderLogs" | "list">;

export const RUN: RunData = {
  rows: RUN_ROWS,
  newRows: { count: 0, onReveal: () => {} },
  loading: false,
  stale: false,
  error: null,
  onRetry: () => {},
  nextCursor: "cursor-2",
  totalCount: 42,
};

export const RUN_LOADING: RunData = { ...RUN, rows: [], loading: true, totalCount: null };

export const RUN_EMPTY: RunData = { ...RUN, rows: [], nextCursor: null, totalCount: 0 };

/** The page query failed with nothing cached: the error sits in the list's frame. */
export const RUN_ERROR: RunData = {
  ...RUN,
  rows: [],
  nextCursor: null,
  totalCount: null,
  error: { message: "Network error: failed to fetch agentTasksPage" },
};

/** Two runs arrived while the reader was on the list: they wait behind the pill. */
export const RUN_NEW_ROWS: RunData = { ...RUN, newRows: { count: 2, onReveal: () => {} } };

export const RUN_LONG: RunData = {
  ...RUN,
  rows: [
    {
      ...TASK,
      id: `${LONG}-7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b`,
      status: "waiting_on_an_external_approval_gate_for_a_very_long_time",
    },
    ...RUN_ROWS.slice(0, 2),
  ],
};

export const LOG_LINES = [
  "2026-09-28T12:00:01Z INFO  agent starting: bdr-outreach v0.1.37",
  "2026-09-28T12:00:02Z INFO  loaded 14 tools from the skill bundle",
  "2026-09-28T12:00:04Z INFO  fetching 25 accounts from the CRM",
  "2026-09-28T12:00:09Z WARN  rate limited by provider, retrying in 2s",
];
