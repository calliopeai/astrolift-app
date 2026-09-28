import type { ComponentProps } from "react";

import type {
  DnsRecordsCard,
  EndpointMetricsPanel,
  GoldenSignalsPanel,
  PromqlQueryPanel,
  TlsCertificatesCard,
  TraceExplorerPanel,
  WorkloadIdentityCard,
} from "@/components/observability";
import type { PodResourceUsage } from "@/components/observability/use-pod-resource-usage";
import type { TopologyEdge, TopologyNode } from "@/components/topology/types";
import type {
  AstroliftAppLogLine,
  AstroliftAppPod,
  AstroliftContainerStatus,
} from "@/graphql/lifecycle/lifecycle.types";

import type { CommandRunnerScreenProps } from "./CommandRunnerScreen";
import type {
  AlertEventsListViewProps,
  AlertRulesPanelViewProps,
  ObservabilityScreenProps,
} from "./ObservabilityScreen";
import type { ShellScreenProps } from "./ShellScreen";
import type { TopologyScreenProps } from "./TopologyScreen";
import type { AlertEvent, AlertRule } from "./use-alert-rules";

/**
 * Hand-typed fixtures for the app Observability, Shell, Topology and Run
 * command tabs (group app-observability-shell-topology-commands).
 */

const noop = () => {};
const noopAsync = async () => {};
const yes = async () => true;

const HOUR = 60 * 60 * 1000;
/** Relative so ages and mute countdowns read the same whenever the story runs. */
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const inFuture = (ms: number) => new Date(Date.now() + ms).toISOString();

export const LONG =
  "customer-facing-checkout-experience-for-the-emea-storefront-with-a-deliberately-long-name";

export const APP = { id: "app-1", name: "Checkout", slug: "checkout" };
export const LONG_APP = { id: "app-long", name: LONG, slug: LONG };

// ─── Pods ────────────────────────────────────────────────────────────────

function container(
  name: string,
  overrides: Partial<AstroliftContainerStatus> = {}
): AstroliftContainerStatus {
  return {
    name,
    image: `ghcr.io/acme/${name}:1.4.2`,
    kind: "primary",
    ready: true,
    restarts: 0,
    state: "running",
    terminatedReason: "",
    waitingReason: "",
    lastRestartAt: null,
    lastRestartReasons: [],
    resources: { cpuRequest: "250m", cpuLimit: "1", memoryRequest: "256Mi", memoryLimit: "512Mi" },
    ...overrides,
  };
}

function pod(name: string, overrides: Partial<AstroliftAppPod> = {}): AstroliftAppPod {
  return {
    name,
    workload: "web",
    status: "Running",
    phase: "Running",
    ready: true,
    restarts: 0,
    age: ago(26 * HOUR),
    node: "ip-10-0-12-34.us-west-2.compute.internal",
    containerStatuses: [container("web"), container("istio-proxy", { kind: "sidecar" })],
    ...overrides,
  };
}

export const PODS: AstroliftAppPod[] = [
  pod("checkout-web-7d9f8b6c4-x2k9p", { restarts: 3 }),
  pod("checkout-web-7d9f8b6c4-q8m2z", { age: ago(40 * 60 * 1000) }),
  pod("checkout-worker-5b7c9d8f6-lp4vn", {
    workload: "worker",
    status: "CrashLoopBackOff",
    phase: "Running",
    ready: false,
    restarts: 17,
    containerStatuses: [container("worker", { ready: false, restarts: 17 })],
  }),
  pod("checkout-migrate-8x2kq", {
    workload: "migrate",
    status: "Succeeded",
    phase: "Succeeded",
    ready: false,
    age: ago(72 * HOUR),
    containerStatuses: [container("migrate", { ready: false, state: "terminated" })],
  }),
];

export const LONG_PODS: AstroliftAppPod[] = [
  pod(`${LONG}-7d9f8b6c4-x2k9p`, {
    workload: LONG,
    node: `${LONG}.compute.internal`,
    containerStatuses: [container(LONG)],
  }),
];

const START = Date.now() - HOUR;
const USAGE: PodResourceUsage = {
  podName: PODS[0].name,
  rangeSeconds: 3600,
  reason: "OK",
  restartCount: 3,
  lastRestartAt: ago(18 * 60 * 1000),
  samples: Array.from({ length: 24 }, (_, i) => ({
    ts: new Date(START + i * 150_000).toISOString(),
    cpuCores: 0.25 + Math.sin(i / 3) * 0.1,
    memoryBytes: 3.1e8 + Math.cos(i / 4) * 4e7,
  })),
};

// ─── Logs + events ───────────────────────────────────────────────────────

export const LOG_LINES: AstroliftAppLogLine[] = [
  "GET /api/cart 200 12ms",
  "GET /api/cart/items 200 31ms",
  "POST /api/checkout 201 142ms",
  "ERROR payment gateway timeout after 30000ms",
  "GET /healthz 200 1ms",
].map((message, i) => ({
  timestamp: new Date(Date.now() - (5 - i) * 4000).toISOString(),
  podName: PODS[i % 2].name,
  container: "web",
  message,
  stream: message.startsWith("ERROR") ? "stderr" : "stdout",
}));

export const LONG_LOG_LINES: AstroliftAppLogLine[] = [
  {
    timestamp: new Date().toISOString(),
    podName: LONG_PODS[0].name,
    container: LONG,
    message: `ERROR ${LONG} ${LONG} ${LONG}`,
    stream: "stderr",
  },
];

export const APP_EVENTS: ObservabilityScreenProps["appEvents"] = [
  {
    id: "ev-1",
    eventType: "deploy.succeeded",
    payload: { deploymentId: "dep-42", message: "Rolled out 1.4.2" },
    occurredAt: ago(20 * 60 * 1000),
  },
  {
    id: "ev-2",
    eventType: "pod.crashloop",
    payload: { pod: PODS[2].name, message: "Back-off restarting failed container" },
    occurredAt: ago(4 * 60 * 1000),
  },
];

// ─── Observability screen ────────────────────────────────────────────────

export const OBSERVABILITY: ObservabilityScreenProps = {
  slug: APP.slug,
  app: APP,
  loading: false,
  pods: PODS,
  podsLoading: false,
  selectedPod: PODS[0].name,
  onPickPod: noop,
  podContainers: ["web", "istio-proxy"],
  selectedContainer: "web",
  onPickContainer: noop,
  podUsage: { usage: USAGE, loading: false, onRetry: noop },
  scopedEnv: null,
  scopedWorkload: null,
  onEnvChange: noop,
  onWorkloadChange: noop,
  scopeOptions: {
    environments: [],
    environmentsLoading: false,
    workloads: [],
    workloadsLoading: false,
  },
  logBuffer: LOG_LINES,
  onClearLogs: noop,
  streaming: true,
  onToggleStreaming: noop,
  allReplicas: true,
  onToggleAllReplicas: noop,
  historicalRange: "live",
  onHistoricalRangeChange: noop,
  isHistorical: false,
  historicalUnavailable: false,
  historicalLoading: false,
  onRefreshHistorical: noop,
  appEvents: APP_EVENTS,
  eventsLoading: false,
  deploymentsHref: "/apps/checkout/deployments",
};

// Props for the metric panels passed in the `panels` slot. Each panel has its
// own stories; these are just enough to show where they sit on the screen.
export const GOLDEN_SIGNALS: ComponentProps<typeof GoldenSignalsPanel> = {
  range: "1h",
  onRangeChange: noop,
  signals: null,
  reason: "NOT_CONFIGURED",
  loading: false,
  onRetry: () => undefined,
  statusBreakdown: null,
  statusLoading: false,
  onStatusRetry: () => undefined,
};
export const ENDPOINT_METRICS: ComponentProps<typeof EndpointMetricsPanel> = {
  metrics: [],
  loading: false,
};
export const TRACES: ComponentProps<typeof TraceExplorerPanel> = {
  traces: [],
  loading: false,
  statusFilter: "ALL",
  onStatusFilterChange: noop,
  spans: {},
  onExpand: noop,
};
export const PROMQL: ComponentProps<typeof PromqlQueryPanel> = {
  open: false,
  onOpenChange: noop,
  discovery: null,
  discoveryLoading: false,
  running: false,
  transportError: null,
  result: null,
  onRun: noop,
};
export const DNS: ComponentProps<typeof DnsRecordsCard> = {
  appSlug: APP.slug,
  data: null,
  loading: false,
  onRefresh: noop,
};
export const TLS: ComponentProps<typeof TlsCertificatesCard> = {
  appSlug: APP.slug,
  data: null,
  loading: false,
  onRefresh: noop,
};
export const IDENTITY: ComponentProps<typeof WorkloadIdentityCard> = {
  appSlug: APP.slug,
  data: null,
  loading: false,
  onRefresh: noop,
};

// ─── Alert rules ─────────────────────────────────────────────────────────

function rule(id: string, overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id,
    name: "High p99 latency",
    target: "app",
    targetId: APP.id,
    severity: "warning",
    predicate: { metric: "latency_p99", comparator: ">", threshold: 500, unit: "ms" },
    notifyChannels: [{ kind: "in_app", ref: "" }],
    isActive: true,
    organizationSlug: "acme",
    createdAt: "2026-09-20T10:00:00Z",
    updatedAt: "2026-09-20T10:00:00Z",
    activeMute: null,
    ...overrides,
  };
}

export const ALERT_RULES: AlertRule[] = [
  rule("rule-1"),
  rule("rule-2", {
    name: "5xx error rate",
    severity: "critical",
    predicate: { metric: "error_rate_5min", comparator: ">", threshold: 1, unit: "%" },
    activeMute: {
      id: "mute-1",
      ttlUntil: inFuture(2 * HOUR + 15 * 60 * 1000),
      reason: "Known noisy after deploy",
      createdBy: "leo",
    },
  }),
  rule("rule-3", {
    name: "Memory saturation",
    severity: "info",
    isActive: false,
    predicate: { expr: "custom" },
  }),
];

export const LONG_ALERT_RULES: AlertRule[] = [
  rule("rule-long", {
    name: LONG,
    predicate: { metric: LONG, comparator: ">", threshold: 123456789 },
  }),
];

export const ALERT_RULES_PANEL: Omit<AlertRulesPanelViewProps, "renderEvents"> = {
  appId: APP.id,
  appName: APP.name,
  rules: ALERT_RULES,
  loading: false,
  busy: false,
  creating: false,
  muting: false,
  onCreate: yes,
  onMute: yes,
  onUnmute: noopAsync,
  onDelete: noopAsync,
};

export const ALERT_EVENTS: AlertEvent[] = [
  {
    id: "aev-1",
    ruleId: "rule-1",
    severity: "warning",
    firedAt: ago(3 * HOUR),
    resolvedAt: ago(2 * HOUR),
    acknowledgedAt: ago(2.5 * HOUR),
    summary: "p99 latency 812ms > 500ms",
    detail: {},
  },
  {
    id: "aev-2",
    ruleId: "rule-1",
    severity: "warning",
    firedAt: ago(20 * 60 * 1000),
    resolvedAt: null,
    acknowledgedAt: null,
    summary: "p99 latency 640ms > 500ms",
    detail: {},
  },
];

export const ALERT_EVENTS_LIST: AlertEventsListViewProps = {
  events: ALERT_EVENTS,
  loading: false,
  acking: false,
  onAck: noopAsync,
};

// ─── Shell ───────────────────────────────────────────────────────────────

export const SHELL: ShellScreenProps = {
  slug: APP.slug,
  app: APP,
  loading: false,
  podRows: PODS,
  selectedPod: PODS[0].name,
  setPickedPod: noop,
  podContainers: ["web", "istio-proxy"],
  selectedContainer: "web",
  setPickedContainer: noop,
  podsLoading: false,
  noPods: false,
  uploading: false,
  uploadedFile: null,
  uploadedCommand: null,
  fetchCommand: null,
  onPickScript: noopAsync,
  onClearUpload: noop,
  deploymentsHref: "/apps/checkout/deployments",
  tokensHref: "/apps/checkout/tokens",
};

export const SHELL_UPLOADED: Partial<ShellScreenProps> = {
  uploadedFile: {
    name: "backfill_orders.py",
    publicUrl: "https://uploads.example.com/acme/backfill_orders.py",
  },
  uploadedCommand: "python /tmp/backfill_orders.py",
  fetchCommand:
    "curl -fsSL -o /tmp/backfill_orders.py 'https://uploads.example.com/acme/backfill_orders.py'",
};

// ─── Topology ────────────────────────────────────────────────────────────

const NODES: TopologyNode[] = [
  {
    id: "ingress",
    type: "ingress",
    label: "Public ingress",
    sublabel: "ALB · 443/TCP",
    status: "running",
    hostnames: ["checkout.acme.dev"],
  },
  { id: "svc-web", type: "service", label: "web", sublabel: "ClusterIP · 8080", status: "running" },
  {
    id: "wl-web",
    type: "workload",
    label: "web",
    sublabel: "Deployment",
    status: "running",
    replicas: { ready: 2, desired: 2 },
  },
  {
    id: "wl-worker",
    type: "workload",
    label: "worker",
    sublabel: "Deployment",
    status: "failed",
    replicas: { ready: 0, desired: 1 },
  },
  {
    id: "ms-db",
    type: "managed-service",
    label: "orders-db",
    sublabel: "postgres",
    status: "running",
  },
];

const EDGES: TopologyEdge[] = [
  { id: "e1", source: "ingress", target: "svc-web", animated: true },
  { id: "e2", source: "svc-web", target: "wl-web" },
  { id: "e3", source: "wl-web", target: "ms-db", label: "DATABASE_URL" },
  { id: "e4", source: "wl-worker", target: "ms-db", label: "DATABASE_URL" },
];

export const TOPOLOGY: TopologyScreenProps = {
  slug: APP.slug,
  app: APP,
  loading: false,
  workloadsLoading: false,
  nodes: NODES,
  edges: EDGES,
  workloadsHref: "/apps/checkout/workloads",
};

export const LONG_NODES: TopologyNode[] = [
  { id: "wl-long", type: "workload", label: LONG, sublabel: LONG, status: "provisioning" },
];

// ─── Run command ─────────────────────────────────────────────────────────

export const COMMAND_RUNNER: CommandRunnerScreenProps = {
  slug: APP.slug,
  workloads: [
    { id: "wl-1", slug: "web", kind: "deployment" },
    { id: "wl-2", slug: "worker", kind: "deployment" },
  ],
  workloadSlug: "web",
  onWorkloadChange: noop,
  containers: [
    { id: "c-1", name: "web", isPrimary: true },
    { id: "c-2", name: "istio-proxy", isPrimary: false },
  ],
  containerName: "web",
  onContainerChange: noop,
  command: "",
  onCommandChange: noop,
  output: [],
  connState: "idle",
  exitCode: null,
  running: false,
  history: [],
  onClearHistory: noop,
  onRun: noop,
  onStop: noop,
};

export const COMMAND_OUTPUT: CommandRunnerScreenProps["output"] = [
  { channel: "stdout", text: "total 8\ndrwxrwxrwt 2 root root 4096 Sep 28 12:00 .\n" },
  { channel: "stderr", text: "ls: cannot access '/tmp/missing': No such file or directory\n" },
  { channel: "system", text: "\n[exit 2]\n" },
];

export const COMMAND_HISTORY = [
  "ls -la /tmp",
  "python manage.py showmigrations",
  "env | sort",
  `echo ${LONG}`,
];
