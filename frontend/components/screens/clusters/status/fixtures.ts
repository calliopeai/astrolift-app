import type { ClusterLiveState } from "@/lib/cluster-heartbeat";

import type { ClusterSummary, PrometheusInstant, PrometheusRange, RangePoint } from "./types";
import type { useClusterHealth } from "./use-cluster-health";
import type { useClusterLifecycleAudit } from "./use-cluster-lifecycle-audit";
import type { useClusterLiveState } from "./use-cluster-live-state";
import type { useClusterMetrics } from "./use-cluster-metrics";
import type { useClusterWorkloadHealth } from "./use-cluster-workload-health";
import type { useRecentClusterWorkflows } from "./use-recent-cluster-workflows";

/** Hand-typed fixtures for the cluster Status / Health / Activity screens. */

const HOUR = 3_600_000;
const BASE = Date.UTC(2026, 8, 28, 12, 0, 0);
const iso = (offsetMs: number) => new Date(BASE - offsetMs).toISOString();

/** The query half every hook returns: no error, and a retry that does nothing here. */
export const QUERY_OK = { error: null as string | null, refetch: () => {} };

/** A query that failed, as the hooks surface it. */
export const QUERY_FAILED = {
  error: "Network error: upstream driver call timed out after 30000ms",
  refetch: () => {},
};

export const CLUSTER: ClusterSummary = {
  id: "cl_01",
  name: "Production east",
  slug: "prod-east",
  providerPluginSlug: "aws-eks",
  region: "us-east-1",
  lifecycle: "managed",
  isActive: true,
};

export const LONG_CLUSTER: ClusterSummary = {
  id: "cl_02",
  name: "Production east shared multi-tenant platform cluster for regulated workloads",
  slug: "prod-east-shared-multi-tenant-platform-cluster-for-regulated-workloads",
  providerPluginSlug: "azure-aks-private-endpoint-with-customer-managed-keys",
  region: "eastus2-availability-zone-3-with-customer-managed-private-link-endpoint",
  lifecycle: "error",
  isActive: false,
};

// ─── Live state ───────────────────────────────────────────────────────
const CONNECTED_STATE: ClusterLiveState = {
  clusterId: "cl_01",
  status: "connected",
  lastHeartbeatAt: iso(12_000),
  heartbeatAgeSeconds: 12,
  heartbeatIntervalSeconds: 30,
  agentProvisioned: true,
  nodeCount: 6,
  nodeReadyCount: 5,
  cpuUtilization: 0.42,
  memoryUtilization: 0.77,
  podTotal: 148,
  podsByNamespace: { "astrolift-apps": 120, "astrolift-system": 28 },
  appReadiness: {
    checkout: { ready: 3, total: 3 },
    "billing-api": { ready: 1, total: 3 },
    "legacy-report-runner": { ready: 0, total: 2 },
  },
  ingressIps: ["34.201.18.77", "52.4.119.203"],
  agentVersion: "v0.1.37",
};

export const LIVE_CONNECTED: ReturnType<typeof useClusterLiveState> = {
  state: CONNECTED_STATE,
  loading: false,
  ...QUERY_OK,
};

export const LIVE_LOADING: ReturnType<typeof useClusterLiveState> = {
  state: null,
  loading: true,
  ...QUERY_OK,
};

export const LIVE_OFFLINE: ReturnType<typeof useClusterLiveState> = {
  state: {
    ...CONNECTED_STATE,
    status: "offline",
    lastHeartbeatAt: iso(5 * HOUR),
    heartbeatAgeSeconds: 18_000,
  },
  loading: false,
  ...QUERY_OK,
};

export const LIVE_NEVER_SEEN: ReturnType<typeof useClusterLiveState> = {
  state: {
    ...CONNECTED_STATE,
    status: "never_seen",
    lastHeartbeatAt: null,
    heartbeatAgeSeconds: null,
    agentProvisioned: false,
    nodeCount: null,
    nodeReadyCount: null,
    cpuUtilization: null,
    memoryUtilization: null,
    podTotal: null,
    podsByNamespace: {},
    appReadiness: {},
    ingressIps: [],
    agentVersion: "",
  },
  loading: false,
  ...QUERY_OK,
};

export const LIVE_LONG: ReturnType<typeof useClusterLiveState> = {
  state: {
    ...CONNECTED_STATE,
    status: "degraded",
    appReadiness: {
      "customer-facing-payments-reconciliation-service-with-a-very-long-slug": {
        ready: 2,
        total: 4,
      },
      checkout: { ready: 3, total: 3 },
    },
    ingressIps: ["2600:1f18:4a3b:9c00:7d2e:1a4f:88b2:0c19", "34.201.18.77"],
    agentVersion: "v0.1.38-rc.2+build.20260928.f1f9f11a0c2e4b7d",
  },
  loading: false,
  ...QUERY_OK,
};

// ─── Prometheus ───────────────────────────────────────────────────────

function series(start: number, amplitude: number, count = 24): RangePoint[] {
  const t0 = Math.floor(BASE / 1000) - count * 60;
  return Array.from({ length: count }, (_, i) => ({
    ts: t0 + i * 60,
    value: Math.max(0, start + amplitude * Math.sin(i / 3)),
  }));
}

const INSTANT: PrometheusInstant = {
  available: true,
  reason: null,
  nodeCount: 6,
  podRunningRatio: 0.96,
  cpuUtilization: 0.42,
  memoryUtilization: 0.81,
  deploymentReadyRatio: 0.92,
};

const RANGE: PrometheusRange = {
  available: true,
  reason: null,
  rangeSeconds: 3600,
  stepSeconds: 60,
  series: [
    {
      metric: "cpu_utilization",
      label: "CPU",
      unit: "ratio",
      current: 0.42,
      points: series(0.4, 0.08),
    },
    {
      metric: "memory_utilization",
      label: "Memory",
      unit: "ratio",
      current: 0.81,
      points: series(0.8, 0.03),
    },
    {
      metric: "pod_running_ratio",
      label: "Pods running",
      unit: "ratio",
      current: 0.96,
      points: series(0.95, 0.02),
    },
    {
      metric: "latency_p99",
      label: "API server p99",
      unit: "seconds",
      current: 0.62,
      points: series(0.5, 0.15),
    },
    {
      metric: "network_rx",
      label: "Network in",
      unit: "bytes_per_sec",
      current: 3_400_000,
      points: series(3_000_000, 600_000),
    },
    {
      metric: "restart_rate",
      label: "Restarts",
      unit: "count",
      current: 0,
      points: [],
    },
  ],
};

export const METRICS: ReturnType<typeof useClusterMetrics> = {
  selectedWindow: "1h",
  onWindowChange: () => {},
  range: RANGE,
  rangeLoading: false,
  instant: INSTANT,
  instantLoading: false,
  ...QUERY_OK,
};

export const METRICS_LOADING: ReturnType<typeof useClusterMetrics> = {
  ...METRICS,
  range: null,
  rangeLoading: true,
  instant: null,
  instantLoading: true,
};

export const METRICS_NO_ENDPOINT: ReturnType<typeof useClusterMetrics> = {
  ...METRICS,
  range: { ...RANGE, available: false, reason: "no_endpoint", series: [] },
  instant: { ...INSTANT, available: false, reason: "no_endpoint" },
};

export const METRICS_UNREACHABLE: ReturnType<typeof useClusterMetrics> = {
  ...METRICS,
  selectedWindow: "24h",
  range: { ...RANGE, available: false, reason: "cluster_internal_endpoint", series: [] },
  instant: { ...INSTANT, available: false, reason: "unreachable" },
};

export const METRICS_LONG: ReturnType<typeof useClusterMetrics> = {
  ...METRICS,
  range: {
    ...RANGE,
    series: [
      {
        metric: "deployment_ready_ratio",
        label: "Deployments ready across every managed namespace in the cluster",
        unit: "ratio",
        current: 0.61,
        points: series(0.6, 0.1),
      },
      {
        metric: "restart_rate",
        label: "Restarts",
        unit: "count",
        current: 4.25,
        points: [{ ts: Math.floor(BASE / 1000), value: 4.25 }],
      },
    ],
  },
};

// ─── Driver health ────────────────────────────────────────────────────
export const WORKLOADS: ReturnType<typeof useClusterWorkloadHealth> = {
  loading: false,
  ...QUERY_OK,
  rows: [
    {
      namespace: "astrolift-apps",
      workloadName: "checkout-web",
      desiredReplicas: 3,
      readyReplicas: 3,
      restartCount24h: 0,
      lastImageDeployedAt: iso(2 * HOUR),
    },
    {
      namespace: "astrolift-apps",
      workloadName: "billing-api",
      desiredReplicas: 3,
      readyReplicas: 1,
      restartCount24h: 7,
      lastImageDeployedAt: iso(26 * HOUR),
    },
    {
      namespace: "astrolift-apps",
      workloadName: "report-runner",
      desiredReplicas: 2,
      readyReplicas: 0,
      restartCount24h: 1,
      lastImageDeployedAt: "",
    },
    {
      namespace: "astrolift-system",
      workloadName: "astrolift-agent",
      desiredReplicas: 1,
      readyReplicas: 1,
      restartCount24h: 0,
      lastImageDeployedAt: iso(72 * HOUR),
    },
  ],
};

export const WORKLOADS_LONG: ReturnType<typeof useClusterWorkloadHealth> = {
  loading: false,
  ...QUERY_OK,
  rows: [
    {
      namespace: "customer-facing-payments-reconciliation-namespace",
      workloadName:
        "payments-reconciliation-nightly-batch-exporter-with-an-unreasonably-long-deployment-name",
      desiredReplicas: 12,
      readyReplicas: 9,
      restartCount24h: 132,
      lastImageDeployedAt: iso(400 * HOUR),
    },
  ],
};

export const HEALTH: ReturnType<typeof useClusterHealth> = {
  loading: false,
  ...QUERY_OK,
  pods: [
    { namespace: "astrolift-apps", phase: "Running", count: 112 },
    { namespace: "astrolift-apps", phase: "Pending", count: 3 },
    { namespace: "astrolift-apps", phase: "Failed", count: 1 },
    { namespace: "astrolift-system", phase: "Running", count: 28 },
    { namespace: "astrolift-system", phase: "Succeeded", count: 4 },
  ],
  events: [
    {
      namespace: "astrolift-apps",
      name: "billing-api-7c9d.17f",
      reason: "BackOff",
      message: "Back-off restarting failed container billing-api in pod billing-api-7c9d",
      type: "Warning",
      count: 14,
      firstSeen: iso(3 * HOUR),
      lastSeen: iso(60_000),
      involvedObject: "Pod/billing-api-7c9d",
    },
    {
      namespace: "astrolift-apps",
      name: "report-runner.1a2",
      reason: "FailedScheduling",
      message: "0/6 nodes are available: 6 Insufficient memory.",
      type: "Warning",
      count: 1,
      firstSeen: iso(HOUR),
      lastSeen: iso(HOUR),
      involvedObject: "Pod/report-runner-5f6b",
    },
    {
      namespace: "astrolift-system",
      name: "agent.9e",
      reason: "Pulled",
      message: "Successfully pulled image",
      type: "Normal",
      count: 1,
      firstSeen: iso(2 * HOUR),
      lastSeen: "",
      involvedObject: "Pod/astrolift-agent-0",
    },
  ],
};

export const HEALTH_LONG: ReturnType<typeof useClusterHealth> = {
  loading: false,
  ...QUERY_OK,
  pods: [
    {
      namespace: "customer-facing-payments-reconciliation-namespace",
      phase: "CrashLoopBackOff",
      count: 1024,
    },
  ],
  events: [
    {
      namespace: "customer-facing-payments-reconciliation-namespace",
      name: "payments.1",
      reason: "FailedCreatePodSandBox",
      message:
        'Failed to create pod sandbox: rpc error: code = Unknown desc = failed to setup network for sandbox "4f0c9b": plugin type="aws-cni" name="aws-cni" failed (add): add cmd: failed to assign an IP address to container because the subnet has no free addresses left',
      type: "Warning",
      count: 311,
      firstSeen: iso(48 * HOUR),
      lastSeen: iso(5 * 60_000),
      involvedObject:
        "Pod/payments-reconciliation-nightly-batch-exporter-with-an-unreasonably-long-deployment-name-6d8f9",
    },
  ],
};

// ─── Temporal + audit ─────────────────────────────────────────────────
export const WORKFLOWS: ReturnType<typeof useRecentClusterWorkflows> = {
  loading: false,
  ...QUERY_OK,
  runs: [
    {
      workflowId: "refresh-cl_01-1",
      workflowType: "RefreshClusterManagement",
      status: "RUNNING",
      startedAt: iso(90_000),
      closedAt: "",
      runId: "r1",
    },
    {
      workflowId: "prereqs-cl_01-1",
      workflowType: "InstallClusterPrereqs",
      status: "COMPLETED",
      startedAt: iso(3 * HOUR),
      closedAt: iso(3 * HOUR - 253_000),
      runId: "r2",
    },
    {
      workflowId: "drift-cl_01-1",
      workflowType: "DriftDetection",
      status: "FAILED",
      startedAt: iso(6 * HOUR),
      closedAt: iso(6 * HOUR - 12_000),
      runId: "r3",
    },
    {
      workflowId: "bring-cl_01-1",
      workflowType: "BringClusterIntoManagement",
      status: "CANCELED",
      startedAt: iso(30 * HOUR),
      closedAt: iso(30 * HOUR - 4_020_000),
      runId: "r4",
    },
  ],
};

export const WORKFLOWS_LONG: ReturnType<typeof useRecentClusterWorkflows> = {
  loading: false,
  ...QUERY_OK,
  runs: [
    {
      workflowId: "long-1",
      workflowType: "ReconcileTenantNetworkPoliciesAcrossEveryManagedNamespaceAndRegion",
      status: "TIMED_OUT",
      startedAt: iso(50 * HOUR),
      closedAt: iso(50 * HOUR - 7_260_000),
      runId: "rl",
    },
  ],
};

export const AUDIT: ReturnType<typeof useClusterLifecycleAudit> = {
  loading: false,
  ...QUERY_OK,
  entries: [
    {
      operation: "refreshClusterManagement",
      variables: {},
      success: true,
      errors: [],
      timestamp: iso(90_000),
      actor: "leo@calliope.ai",
    },
    {
      operation: "installClusterPrereqs",
      variables: {},
      success: false,
      errors: ["Helm release astrolift-ingress failed: timed out waiting for the condition"],
      timestamp: iso(3 * HOUR),
      actor: "leo@calliope.ai",
    },
    {
      operation: "registerCluster",
      variables: {},
      success: true,
      errors: [],
      timestamp: iso(80 * HOUR),
      actor: null,
    },
  ],
};

export const AUDIT_LONG: ReturnType<typeof useClusterLifecycleAudit> = {
  loading: false,
  ...QUERY_OK,
  entries: [
    {
      operation: "decommissionClusterAndReleaseAllProviderResourcesImmediately",
      variables: {},
      success: false,
      errors: [
        "Decommission refused: 14 applications still have live environments on this cluster, including customer-facing-payments-reconciliation-service-with-a-very-long-slug; move or delete them first.",
      ],
      timestamp: iso(5 * HOUR),
      actor: "platform-automation-service-account@operations.calliope.ai",
    },
  ],
};
