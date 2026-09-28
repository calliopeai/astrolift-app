import type { HeartbeatStatus } from "@/lib/cluster-heartbeat";

// ─── Window config (drives every range query) ────────────────────────
// stepSeconds doubles as the CloudWatch period — all values are 60s
// multiples so they're valid CloudWatch periods as well as Prometheus
// steps.
export const WINDOWS = [
  { label: "1h", rangeSeconds: 3_600, stepSeconds: 60 },
  { label: "6h", rangeSeconds: 21_600, stepSeconds: 300 },
  { label: "24h", rangeSeconds: 86_400, stepSeconds: 900 },
] as const;

export type MetricsWindow = (typeof WINDOWS)[number];
export type WindowLabel = MetricsWindow["label"];

// ─── GraphQL response shapes (typed locally — the clusters surface uses
// plain gql documents + inline interfaces rather than codegen output,
// mirroring cluster-status-client). ─────────────────────────────────
export interface MetricsCluster {
  id: string;
  slug: string;
  name: string;
  providerPluginSlug: string;
  region: string;
  isActive: boolean;
  heartbeatStatus: HeartbeatStatus;
  heartbeatAgeSeconds: number | null;
}

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

export interface PromInstant {
  available: boolean;
  reason: string | null;
  nodeCount: number | null;
  podRunningRatio: number | null;
  cpuUtilization: number | null;
  memoryUtilization: number | null;
  deploymentReadyRatio: number | null;
}

export interface PromRange {
  available: boolean;
  reason: string | null;
  rangeSeconds: number;
  stepSeconds: number;
  series: RangeSeries[];
}

export interface SystemMetrics {
  available: boolean;
  reason: string | null;
  source: string;
  appNamespace: string;
  rangeSeconds: number;
  stepSeconds: number;
  series: RangeSeries[];
}

// ─── Value formatting + tone (shared across Prometheus + CloudWatch
// series; mirrors cluster-status-client so the dataviz reads
// identically across surfaces). ──────────────────────────────────────
export function fmtValue(value: number | null, unit: string): string {
  if (value === null) return "—";
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "count") return value < 0.1 ? "0" : value.toFixed(2);
  if (unit === "rps") return `${value.toFixed(value < 10 ? 2 : 0)} req/s`;
  if (unit === "seconds") {
    if (value < 0.001) return `${(value * 1_000_000).toFixed(0)}µs`;
    if (value < 1) return `${(value * 1000).toFixed(0)}ms`;
    return `${value.toFixed(2)}s`;
  }
  if (unit === "bytes_per_sec") {
    if (value < 1024) return `${value.toFixed(0)} B/s`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB/s`;
    if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB/s`;
    return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB/s`;
  }
  return value.toFixed(2);
}

export const TONE_COLORS = {
  ok: { text: "text-success-fg", stroke: "var(--success)", fill: "var(--success-bg)" },
  warn: { text: "text-warning-fg", stroke: "var(--warning)", fill: "var(--warning-bg)" },
  bad: { text: "text-destructive", stroke: "var(--danger)", fill: "var(--danger-bg)" },
  neutral: { text: "text-foreground", stroke: "var(--info)", fill: "var(--info-bg)" },
} as const;

export type Tone = keyof typeof TONE_COLORS;

export function utilizationTone(v: number | null): Tone {
  if (v === null) return "neutral";
  if (v < 0.7) return "ok";
  if (v < 0.9) return "warn";
  return "bad";
}

export function healthTone(v: number | null): Tone {
  if (v === null) return "neutral";
  if (v >= 0.9) return "ok";
  if (v >= 0.7) return "warn";
  return "bad";
}

// Tone for a range/system series based on its metric + latest value.
// Utilization/latency: high is bad. Health ratios: high is good.
// Throughput/counts: neutral (volume isn't inherently good or bad).
export function seriesTone(series: RangeSeries): Tone {
  const v = series.current;
  if (v === null) return "neutral";
  if (series.metric === "pod_running_ratio" || series.metric === "deployment_ready_ratio") {
    return healthTone(v);
  }
  if (series.metric === "error_rate") {
    if (v < 0.01) return "ok";
    if (v < 0.05) return "warn";
    return "bad";
  }
  if (series.metric === "latency_p99" || series.metric === "latency_p95") {
    if (v < 0.1) return "ok";
    if (v < 0.5) return "warn";
    return "bad";
  }
  if (series.metric === "restart_rate") {
    if (v === 0) return "ok";
    if (v < 1) return "warn";
    return "bad";
  }
  if (series.metric === "network_rx" || series.metric === "request_rate") return "neutral";
  if (series.unit === "count") return "neutral";
  return utilizationTone(v);
}

export function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}
