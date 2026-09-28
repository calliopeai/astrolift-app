import { fakeController } from "@/components/data-table/fixtures";
import type {
  AgentTaskTransition,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentTask,
  FleetTaskDispatcher,
} from "@/graphql/agents/agents.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { FunctionsScreenProps } from "../functions/FunctionsScreen";
import type { FunctionWorkloadsTableProps } from "../functions/FunctionWorkloadsTable";

import type { FleetClusterLiveness } from "./FleetMap";
import type { FleetMapPanelProps } from "./FleetMapPanel";
import type { FleetOverviewScreenProps } from "./FleetOverviewScreen";

/**
 * Hand-typed fixtures for the fleet-functions-logs group: /fleet, /fleet/map
 * and /functions. /logs and /traces are static and take no props.
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
  refresh: noop,
  agentTable: fakeController<AstroliftAgentListItem>({ rows: AGENTS, totalCount: AGENTS.length }),
  taskTable: fakeController<AstroliftAgentTask>({ rows: TASKS, totalCount: TASKS.length }),
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
  agentTable: fakeController<AstroliftAgentListItem>({ state: "loading" }),
  taskTable: fakeController<AstroliftAgentTask>({ state: "loading" }),
};

export const FLEET_OVERVIEW_EMPTY: FleetOverviewFixture = {
  ...FLEET_OVERVIEW,
  agentCount: 0,
  runningAgentCount: 0,
  runtimeCount: 0,
  activeTaskCount: 0,
  incidentCount: 0,
  runtimes: [],
  agentTable: fakeController<AstroliftAgentListItem>({ state: "empty" }),
  taskTable: fakeController<AstroliftAgentTask>({ state: "empty" }),
};

export const FLEET_OVERVIEW_ERROR: FleetOverviewFixture = {
  ...FLEET_OVERVIEW_EMPTY,
  runtimeCount: undefined,
  agentTable: fakeController<AstroliftAgentListItem>({
    state: "error",
    error: new Error("Network error: failed to fetch agentFleetPage"),
  }),
  taskTable: fakeController<AstroliftAgentTask>({
    state: "error",
    error: new Error("Network error: failed to fetch agentTasksPage"),
  }),
};

export const FLEET_OVERVIEW_LONG: FleetOverviewFixture = {
  ...FLEET_OVERVIEW,
  runtimes: RUNTIMES.map((r) => ({ ...r, name: `${r.name} ${LONG}`, runtime: LONG })),
  agentTable: fakeController<AstroliftAgentListItem>({
    rows: AGENTS.map((a) => ({ ...a, name: LONG, projectSlug: LONG, runningCount: 1234 })),
  }),
  taskTable: fakeController<AstroliftAgentTask>({
    rows: TASKS.map((t) => ({ ...t, agentName: LONG, projectSlug: LONG, status: "timed_out" })),
  }),
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

// ── /functions ──────────────────────────────────────────────────────────────

const WORKLOAD: AstroliftWorkload = {
  id: "wl-fn-1",
  name: "resize-image",
  slug: "resize-image",
  kind: "function",
  registeredAppSlug: "media",
  concurrencyPolicy: "",
  cpuLimit: "500m",
  cpuRequest: "100m",
  memoryLimit: "512Mi",
  memoryRequest: "128Mi",
  hpaMinReplicas: 0,
  hpaMaxReplicas: 20,
  hpaTargetCpuPct: 70,
  inClusterServiceFqdn: "resize-image.media.svc.cluster.local",
  isPublic: false,
  replicas: 0,
  schedule: "",
  storageClass: "",
  storageSize: "",
  // Generated JSON scalar intersected with the facade's array type.
  volumes: json([]) as AstroliftWorkload["volumes"],
};

export const FUNCTION_WORKLOADS_ROWS: AstroliftWorkload[] = [
  WORKLOAD,
  {
    ...WORKLOAD,
    id: "wl-fn-2",
    name: "stripe-webhook",
    slug: "stripe-webhook",
    registeredAppSlug: "billing",
    hpaMaxReplicas: null,
  },
  { ...WORKLOAD, id: "wl-fn-3", name: "queue-drain", slug: "queue-drain", hpaMinReplicas: null },
];

export const FUNCTION_WORKLOADS: FunctionWorkloadsTableProps = {
  table: fakeController<AstroliftWorkload>({ rows: FUNCTION_WORKLOADS_ROWS }),
};

export const FUNCTION_WORKLOADS_LOADING: FunctionWorkloadsTableProps = {
  table: fakeController<AstroliftWorkload>({ state: "loading" }),
};

export const FUNCTION_WORKLOADS_EMPTY: FunctionWorkloadsTableProps = {
  table: fakeController<AstroliftWorkload>({ state: "empty", hasNext: true }),
};

export const FUNCTION_WORKLOADS_ERROR: FunctionWorkloadsTableProps = {
  table: fakeController<AstroliftWorkload>({
    state: "error",
    error: new Error("Network error: failed to fetch astroliftWorkloadsPage"),
  }),
};

export const FUNCTION_WORKLOADS_LONG: FunctionWorkloadsTableProps = {
  table: fakeController<AstroliftWorkload>({
    rows: FUNCTION_WORKLOADS_ROWS.map((w) => ({ ...w, name: LONG, registeredAppSlug: LONG })),
  }),
};

/** Everything but the fleet slot, which stories fill with a FunctionWorkloadsTable. */
export const FUNCTIONS_TAB: Omit<FunctionsScreenProps, "fleet"> = {
  tab: "fleet",
  setTab: noop,
};
