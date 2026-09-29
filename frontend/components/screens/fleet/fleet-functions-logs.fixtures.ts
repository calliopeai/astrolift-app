import type {
  AgentTaskTransition,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentTask,
  FleetTaskDispatcher,
} from "@/graphql/agents/agents.types";

import type { FleetClusterLiveness } from "./FleetMap";
import type { FleetMapPanelProps } from "./FleetMapPanel";
import type { FleetOverviewScreenProps } from "./FleetOverviewScreen";

/**
 * Hand-typed fixtures for the fleet-functions-logs group: /fleet and
 * /fleet/map (Functions keeps its own now). /logs and /traces are static and
 * take no props.
 */

/** The generated JSON scalar is typed Record<string, unknown>; real values can be any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const noop = () => {};

export const LONG =
  "platform-team-shared-production-research-agent-with-a-deliberately-long-name-that-keeps-going";

const MINUTES_AGO = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

// ── /fleet ──────────────────────────────────────────────────────────────────

const AGENT: AstroliftAgentListItem = {
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
  runningCount: 2,
};

export const AGENTS: AstroliftAgentListItem[] = [
  AGENT,
  { ...AGENT, id: "wl-2", name: "Support Bot", slug: "support-bot", runningCount: 0 },
  {
    ...AGENT,
    id: "wl-3",
    name: "Nightly Sync",
    slug: "nightly-sync",
    projectSlug: "",
    runningCount: 0,
  },
];

const TASK: AstroliftAgentTask = {
  id: "task-1",
  agentSlug: "research-scout",
  agentName: "Research Scout",
  projectSlug: "growth",
  status: "running",
  callbackUrl: "",
  result: null,
  failureMessage: "",
  createdAt: MINUTES_AGO(2),
  startedAt: MINUTES_AGO(1),
  finishedAt: null,
  vncEnabled: false,
  vncUrl: "",
  snapshotUrl: null,
  podName: "research-scout-7f3c2a91",
  namespace: "agents-growth",
};

export const TASKS: AstroliftAgentTask[] = [
  TASK,
  { ...TASK, id: "task-2", status: "queued", createdAt: MINUTES_AGO(4), startedAt: null },
  {
    ...TASK,
    id: "task-3",
    agentSlug: "support-bot",
    agentName: "Support Bot",
    status: "failed",
    failureMessage: "pod exited 1",
    createdAt: MINUTES_AGO(95),
    finishedAt: MINUTES_AGO(90),
  },
  {
    ...TASK,
    id: "task-4",
    agentSlug: "nightly-sync",
    agentName: "",
    projectSlug: "",
    status: "timed_out",
    createdAt: MINUTES_AGO(60 * 30),
  },
  {
    ...TASK,
    id: "task-5",
    agentSlug: "",
    agentName: "",
    status: "completed",
    createdAt: MINUTES_AGO(60 * 50),
    finishedAt: MINUTES_AGO(60 * 49),
  },
];

const RUNTIME: AstroliftAgentEnvironmentSpec = {
  id: "spec-1",
  slug: "python-claude",
  name: "Python + Claude",
  runtime: "python",
  imageTag: "3.12",
  agentType: "claude",
  vncEnabled: false,
  secretRefs: json([]),
  managedModel: true,
};

export const RUNTIMES: AstroliftAgentEnvironmentSpec[] = [
  RUNTIME,
  { ...RUNTIME, id: "spec-2", slug: "node-codex", name: "Node + Codex", runtime: "node" },
  {
    ...RUNTIME,
    id: "spec-3",
    slug: "desktop",
    name: "Desktop (VNC)",
    runtime: "ubuntu",
    vncEnabled: true,
  },
];

/** Everything but the map slot, which stories fill with a FleetMapPanel. */
export type FleetOverviewFixture = Omit<FleetOverviewScreenProps, "map">;

export const FLEET_OVERVIEW: FleetOverviewFixture = {
  agentCount: AGENTS.length,
  runningAgentCount: 1,
  runtimeCount: RUNTIMES.length,
  activeTaskCount: 2,
  incidentCount: 2,
  loading: false,
  runtimes: RUNTIMES,
  runtimesLoading: false,
  runtimesError: null,
  refresh: noop,
  recentTasks: TASKS,
  tasksLoading: false,
  tasksError: null,
  agents: AGENTS,
  agentsLoading: false,
  agentsError: null,
};

export const FLEET_OVERVIEW_LOADING: FleetOverviewFixture = {
  ...FLEET_OVERVIEW,
  agentCount: 0,
  runningAgentCount: 0,
  runtimeCount: undefined,
  activeTaskCount: 0,
  incidentCount: 0,
  loading: true,
  runtimes: [],
  runtimesLoading: true,
  recentTasks: [],
  tasksLoading: true,
  agents: [],
  agentsLoading: true,
};

export const FLEET_OVERVIEW_EMPTY: FleetOverviewFixture = {
  ...FLEET_OVERVIEW,
  agentCount: 0,
  runningAgentCount: 0,
  runtimeCount: 0,
  activeTaskCount: 0,
  incidentCount: 0,
  runtimes: [],
  recentTasks: [],
  agents: [],
};

export const FLEET_OVERVIEW_ERROR: FleetOverviewFixture = {
  ...FLEET_OVERVIEW_EMPTY,
  runtimeCount: undefined,
  agentsError: "Network error: failed to fetch agentFleet",
  tasksError: "Network error: failed to fetch agentTasks",
  runtimesError: "Network error: failed to fetch agentEnvironmentSpecs",
};

export const FLEET_OVERVIEW_LONG: FleetOverviewFixture = {
  ...FLEET_OVERVIEW,
  runtimes: RUNTIMES.map((r) => ({ ...r, name: `${r.name} ${LONG}`, runtime: LONG })),
  agents: AGENTS.map((a) => ({ ...a, name: LONG, projectSlug: LONG, runningCount: 1234 })),
  recentTasks: TASKS.map((t) => ({
    ...t,
    agentName: LONG,
    projectSlug: LONG,
    status: "timed_out",
  })),
};

// ── /fleet/map ──────────────────────────────────────────────────────────────

const DISPATCHER: FleetTaskDispatcher = {
  id: "disp-1",
  name: "us-west-2 dispatcher",
  slug: "usw2",
  cloud: "aws",
  region: "us-west-2",
  clusterId: "cl-1",
  clusterName: "prod-usw2",
};

const TRANSITION: AgentTaskTransition = {
  id: "7f3c2a91-0000-0000-0000-000000000001",
  status: "running",
  createdAt: MINUTES_AGO(3),
  updatedAt: MINUTES_AGO(1),
  queuedAt: MINUTES_AGO(3),
  provisioningAt: MINUTES_AGO(2),
  startedAt: MINUTES_AGO(1),
  finishedAt: null,
  podName: "research-scout-7f3c2a91",
  namespace: "agents-growth",
  dispatcher: DISPATCHER,
};

export const TRANSITIONS: AgentTaskTransition[] = [
  TRANSITION,
  {
    ...TRANSITION,
    id: "7e11b0c4-0000-0000-0000-000000000002",
    status: "completed",
    startedAt: MINUTES_AGO(5),
    finishedAt: MINUTES_AGO(3),
    podName: "support-bot-7e11b0c4",
  },
  {
    ...TRANSITION,
    id: "7d02e5f7-0000-0000-0000-000000000003",
    status: "queued",
    startedAt: null,
    podName: "",
    dispatcher: null,
  },
  {
    ...TRANSITION,
    id: "7c9a1d33-0000-0000-0000-000000000004",
    status: "failed",
    startedAt: MINUTES_AGO(8),
    finishedAt: MINUTES_AGO(7),
    podName: "triage-agent-7c9a1d33",
    dispatcher: {
      ...DISPATCHER,
      id: "disp-2",
      name: "eu-central-1 dispatcher",
      region: "eu-central-1",
      clusterId: "cl-2",
      clusterName: "prod-euc1",
    },
  },
];

export const CLUSTER_LIVENESS: FleetClusterLiveness = new Map([
  ["cl-1", { status: "connected", ageSeconds: 12 }],
  ["cl-2", { status: "degraded", ageSeconds: 240 }],
]);

export const FLEET_MAP: FleetMapPanelProps = {
  tasks: TRANSITIONS,
  clusterLiveness: CLUSTER_LIVENESS,
  errorMessage: null,
  firstLoad: false,
};

export const FLEET_MAP_LOADING: FleetMapPanelProps = {
  ...FLEET_MAP,
  tasks: [],
  firstLoad: true,
};

export const FLEET_MAP_EMPTY: FleetMapPanelProps = { ...FLEET_MAP, tasks: [] };

export const FLEET_MAP_ERROR: FleetMapPanelProps = {
  ...FLEET_MAP,
  tasks: [],
  errorMessage: "Network error: failed to fetch agentTaskTransitionsSince",
};

export const FLEET_MAP_LONG: FleetMapPanelProps = {
  ...FLEET_MAP,
  tasks: TRANSITIONS.map((t) => ({
    ...t,
    podName: `${t.podName || "pod"}-${LONG}`,
    dispatcher: t.dispatcher && { ...t.dispatcher, name: LONG, clusterName: LONG },
  })),
};
