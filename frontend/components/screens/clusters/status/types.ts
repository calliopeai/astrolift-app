import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

/** The slice of a cluster the tab frame shows. */
export type ClusterSummary = Pick<
  AstroliftTenantCluster,
  "id" | "name" | "slug" | "providerPluginSlug"
>;

// ─── Prometheus windows ───────────────────────────────────────────────
export const WINDOWS = [
  { label: "1h", rangeSeconds: 3_600, stepSeconds: 60 },
  { label: "6h", rangeSeconds: 21_600, stepSeconds: 300 },
  { label: "24h", rangeSeconds: 86_400, stepSeconds: 900 },
  { label: "3d", rangeSeconds: 259_200, stepSeconds: 3600 },
  { label: "7d", rangeSeconds: 604_800, stepSeconds: 7200 },
] as const;

export type WindowLabel = (typeof WINDOWS)[number]["label"];

export interface RangePoint {
  ts: number;
  value: number;
}

export interface RangeSeries {
  metric: string;
  label: string;
  unit: string;
  current: number | null;
  points: RangePoint[];
}

export interface PrometheusRange {
  available: boolean;
  reason: string | null;
  rangeSeconds: number;
  stepSeconds: number;
  series: RangeSeries[];
}

export interface PrometheusInstant {
  available: boolean;
  reason: string | null;
  nodeCount: number | null;
  podRunningRatio: number | null;
  cpuUtilization: number | null;
  memoryUtilization: number | null;
  deploymentReadyRatio: number | null;
}

// ─── Driver health ────────────────────────────────────────────────────
export interface PodPhase {
  namespace: string;
  phase: string;
  count: number;
}

export interface ClusterEvent {
  namespace: string;
  name: string;
  reason: string;
  message: string;
  type: string;
  count: number;
  firstSeen: string;
  lastSeen: string;
  involvedObject: string;
}

export interface ClusterHealthPayload {
  clusterId: string;
  pods: PodPhase[];
  events: ClusterEvent[];
}

export interface WorkloadRow {
  namespace: string;
  workloadName: string;
  desiredReplicas: number;
  readyReplicas: number;
  restartCount24h: number;
  lastImageDeployedAt: string;
}

// ─── Temporal + audit ─────────────────────────────────────────────────
export interface WorkflowRun {
  workflowId: string;
  workflowType: string;
  status: string;
  startedAt: string;
  closedAt: string;
  runId: string;
}

export interface AuditRow {
  operation: string;
  variables: Record<string, unknown>;
  success: boolean;
  errors: string[];
  timestamp: string;
  actor: string | null;
}
