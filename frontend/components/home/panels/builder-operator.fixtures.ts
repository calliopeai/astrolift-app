/** Hand-typed fixtures for the Builder and Operator panels' stories and tests. */

import type { ActivityFeedProps } from "@/components/ActivityFeed";
import { LONG_ARN, LONG_SHA, LONG_URL } from "@/components/panel/fixtures";
import { EVENTS, LONG_EVENT } from "@/components/screens/alerts/alerts.fixtures";
import { cluster, LONG_CLUSTER } from "@/components/screens/clusters/list/fixtures";
import type { AstroliftBudget, AstroliftCostForecast } from "@/graphql/billing/billing.types";
import type { AstroliftDeploymentMetrics } from "@/graphql/lifecycle/lifecycle.types";
import type {
  AstroliftActivityItem,
  AstroliftWorkflowRun,
} from "@/graphql/operations/operations.types";

import { HOME_PANELS } from "../registry";
import { type KpisPanelData, kpiFigures } from "./builder-operator-model";
import type { HomeAgentTask } from "./home-reads";

export const NOW = Date.UTC(2026, 8, 28, 12, 0);
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
const json = (v: unknown) => v as Record<string, unknown>;
const noop = () => {};

export const READY = { loading: false, error: null, onRetry: noop };
export const LOADING = { loading: true, error: null, onRetry: noop };
export const FAILED = {
  loading: false,
  error: "Network error: upstream driver call timed out after 30000ms",
  onRetry: noop,
};

export const PANEL = HOME_PANELS;

// ---------------------------------------------------------------- activity

const activityItem = (
  id: string,
  action: string,
  targetLabel: string,
  actorDisplay: string,
  minutes: number,
  eventType = `deploy.${action}`
): AstroliftActivityItem => ({
  id,
  action,
  actorDisplay,
  eventType,
  targetKind: "deployment",
  targetLabel,
  targetHref: `/deployments/${id}`,
  payload: {},
  occurredAt: ago(minutes),
});

export const ACTIVITY: ActivityFeedProps = {
  items: [
    activityItem("a1", "succeeded", "checkout → production", "leo", 2),
    activityItem("a2", "failed", "billing-api → staging", "astrolift-bot", 14),
    activityItem("a3", "approved", "checkout → production", "eric@example.com", 40),
    activityItem("a4", "scaled", "report-runner", "keith@example.com", 90, "scale.updated"),
    activityItem("a5", "brought_in", "conflict-astro", "leo", 60 * 26, "cluster.managed"),
  ],
  loading: false,
  error: null,
  hasMore: true,
  loadingMore: false,
  onLoadMore: noop,
  onRetry: noop,
};

export const ACTIVITY_LONG: ActivityFeedProps = {
  ...ACTIVITY,
  items: [
    activityItem("l1", "rotated", LONG_ARN, `svc-${LONG_SHA}@example.com`, 3, "secret.rotated"),
    activityItem("l2", "synced", LONG_URL, "astrolift-bot", 5, "config.synced"),
  ],
};

// ---------------------------------------------------------------- KPIs

export const METRICS: AstroliftDeploymentMetrics = {
  windowDays: 30,
  total: 14,
  succeeded: 13,
  failed: 1,
  rolledBack: 0,
  inFlight: 1,
  successRate: 13 / 14,
  meanDurationSeconds: 96,
  p95DurationSeconds: 190,
  dailySucceeded: [],
  dailyFailed: [],
  dailyMeanDurationSeconds: [],
};

export const FORECAST: AstroliftCostForecast = {
  mtdCents: 124_000,
  previousMonthCents: 180_000,
  deltaPct: -31.1,
  projectedMonthlyCents: 191_000,
  confidence: "HIGH" as AstroliftCostForecast["confidence"],
  currency: "USD",
};

const task = (id: string, patch: Partial<HomeAgentTask> = {}): HomeAgentTask => ({
  id,
  agentSlug: "support-bot",
  agentName: "Support bot",
  projectSlug: "support",
  status: "completed",
  failureMessage: null,
  createdAt: ago(10),
  startedAt: ago(9),
  finishedAt: ago(8),
  ...patch,
});

export const AGENT_RUNS: HomeAgentTask[] = [
  task("7f3c2a10-9b1d-4e2f-8a7c-000000000001", { status: "running", finishedAt: null }),
  task("7e11b0c2-3a4f-4e5d-8c9b-000000000002", {
    status: "failed",
    failureMessage: "timed out after 300s",
    createdAt: ago(20),
    startedAt: ago(19),
  }),
  task("7d00a9b1-293e-4d4c-9b8a-000000000003", {
    agentSlug: "bdr-outreach",
    agentName: "BDR outreach",
    createdAt: ago(45),
    startedAt: ago(44),
  }),
  task("7cff98a0-182d-4c3b-8a79-000000000004", {
    status: "queued",
    startedAt: null,
    createdAt: ago(1),
  }),
  task("7bee8790-071c-4b2a-7968-000000000005", { createdAt: ago(120), startedAt: ago(119) }),
];

export const AGENT_RUNS_LONG: HomeAgentTask[] = [
  task(LONG_SHA, {
    agentSlug: `agent-${LONG_SHA}`,
    agentName: LONG_ARN,
    status: "provisioning",
  }),
];

export const KPIS: KpisPanelData = {
  windowDays: 30,
  figures: kpiFigures({
    metrics: METRICS,
    runs: { runs: AGENT_RUNS, capped: false, now: NOW },
    forecast: FORECAST,
    windowDays: 30,
  }),
  ...READY,
};

/** The first fetches in flight: every figure a dash, the strip a skeleton. */
export const KPIS_LOADING: KpisPanelData = {
  windowDays: 30,
  figures: kpiFigures({
    metrics: null,
    runs: { runs: [], capped: false, now: NOW, loading: true },
    forecast: null,
    windowDays: 30,
  }),
  ...LOADING,
};

/** Only Agents: deploy figures and spend are left out. */
export const KPIS_AGENTS_ONLY: KpisPanelData = {
  windowDays: 30,
  figures: kpiFigures({
    runs: { runs: AGENT_RUNS, capped: true, now: NOW },
    windowDays: 30,
  }),
  ...READY,
};

/** No deploys in the period: success and p95 have nothing to say. */
export const KPIS_EMPTY: KpisPanelData = {
  windowDays: 30,
  figures: kpiFigures({
    metrics: {
      ...METRICS,
      total: 0,
      succeeded: 0,
      failed: 0,
      successRate: 0,
      p95DurationSeconds: null,
    },
    runs: { runs: [], capped: false, now: NOW },
    forecast: { ...FORECAST, mtdCents: 0, projectedMonthlyCents: 0 },
    windowDays: 30,
  }),
  ...READY,
};

// ---------------------------------------------------------------- alerts

export const ALERTS = [EVENTS[0]!, EVENTS[1]!];
export const ALERTS_LONG = [LONG_EVENT];

// ---------------------------------------------------------------- clusters

export const CLUSTERS = [
  cluster("staging-eu", { name: "staging-eu", heartbeatStatus: "offline", region: "eu-west-1" }),
  cluster("edge-sao", {
    name: "edge-sao",
    lifecycle: "error",
    lastManagementError: "helm install failed",
  }),
  cluster("prod-us", { name: "prod-us" }),
  cluster("prod-eu", { name: "prod-eu", heartbeatStatus: "degraded" }),
  cluster("dev", { name: "dev", providerPluginSlug: "kind", region: "local" }),
  cluster("new-gke", { name: "new-gke", providerPluginSlug: "gke", heartbeatStatus: "never_seen" }),
];

export const CLUSTERS_HEALTHY = [cluster("prod-us", { name: "prod-us" })];

export const CLUSTERS_LONG = [LONG_CLUSTER];

// ---------------------------------------------------------------- platform activity

const platformRun = (id: string, patch: Partial<AstroliftWorkflowRun>): AstroliftWorkflowRun => ({
  id,
  workflowId: `deploy-checkout-${id}`,
  runId: `run-${id}`,
  workflowKind: "DeployAppWorkflow",
  status: "completed",
  startedAt: ago(5),
  endedAt: ago(4),
  failure: json({}),
  organizationId: "org-1",
  registeredAppId: "app-1",
  ...patch,
});

export const PLATFORM_RUNS: AstroliftWorkflowRun[] = [
  platformRun("p1", { status: "running", endedAt: null }),
  platformRun("p2", {
    status: "failed",
    workflowId: "deploy-billing-api-7",
    failure: json({ message: "ImagePullBackOff: sha-4f1c9e2 not found" }),
    startedAt: ago(30),
  }),
  platformRun("p3", {
    workflowKind: "BringClusterIntoManagementWorkflow",
    workflowId: "bring-in-prod-eu",
    startedAt: ago(90),
  }),
  platformRun("p4", {
    workflowKind: "OnboardAppWorkflow",
    workflowId: "onboard-report-runner",
    startedAt: ago(60 * 26),
  }),
];

export const PLATFORM_RUNS_LONG: AstroliftWorkflowRun[] = [
  platformRun("pl", {
    status: "failed",
    workflowKind: `DeployAppWorkflow${LONG_SHA}`,
    workflowId: LONG_URL,
    failure: json({ message: `AccessDenied: ${LONG_ARN} is not authorized` }),
  }),
];

// ---------------------------------------------------------------- spend & quota

export const BUDGET: AstroliftBudget = {
  id: "b-org",
  scopeKind: "ORG",
  scopeId: "org-1",
  amountCents: 200_000,
  currency: "USD",
  period: "monthly",
  currentSpendCents: 124_000,
  alertsAtPct: [80, 100],
};

export const BUDGET_NEAR = { ...BUDGET, amountCents: 140_000 };
export const BUDGET_OVER = { ...BUDGET, amountCents: 100_000 };
