import type { ActivityFeedProps } from "@/components/ActivityFeed";
import type { AstroliftActivityItem } from "@/graphql/operations/operations.types";

import type { DashboardScreenProps } from "./DashboardScreen";

/** Hand-typed fixtures for the overview dashboard (dashboard-build-dev group). */

const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

const ALL_TILES: DashboardScreenProps["tileIds"] = [
  "teams",
  "projects",
  "apps",
  "deployments",
  "agents",
  "workflows",
  "costMtd",
];

/** A fully entitled viewer with a busy fleet. */
export const DASHBOARD: Omit<DashboardScreenProps, "onboarding" | "activity"> = {
  errorMessage: null,
  tileIds: ALL_TILES,
  onReorderTiles: () => {},
  canViewApps: true,
  canViewAgents: true,
  canViewWorkflows: true,
  canViewProjects: true,
  canViewAudit: true,
  teamsCount: 4,
  teamsLoading: false,
  projectsCount: 11,
  projectsLoading: false,
  appsCount: 23,
  healthLoading: false,
  deployedCount: 19,
  agentsCount: 6,
  agentFleetLoading: false,
  workflowsCount: 3,
  workflowDefinitionsLoading: false,
  forecast: { mtdCents: 418_000, currency: "USD", deltaPct: 6.2, previousMonthCents: 393_600 },
  costForecastLoading: false,
  inFlightCount: 2,
  activeAgentTasks: [
    {
      id: "task-7f3a",
      agentName: "EMR Triage Intake",
      agentSlug: "emr-triage-intake",
      projectSlug: "emr-bug-triage",
      status: "running",
    },
    {
      id: "task-8b21",
      agentName: "",
      agentSlug: "release-notes-writer",
      projectSlug: "",
      status: "queued",
    },
  ],
  runningAgentTasks: 1,
  queuedAgentTasks: 1,
  activeWorkflowRuns: [
    {
      guid: "run-1",
      definitionSlug: "emr-triage",
      definitionName: "EMR Triage",
      projectSlug: "emr-bug-triage",
      currentStageOrder: 1,
      currentStageRole: "decide",
    },
    {
      guid: "run-2",
      definitionSlug: "nightly-refresh",
      definitionName: "Nightly refresh",
      projectSlug: "",
      currentStageOrder: null,
      currentStageRole: "",
    },
  ],
  recentAgentFailures: 1,
  recentWorkflowFailures: 0,
  runningCount: 17,
  failingCount: 1,
  recentFailureCount: 2,
  noDeployCount: 3,
  recentProjects: [
    { id: "p1", slug: "checkout", name: "Checkout", team: { slug: "payments" } },
    { id: "p2", slug: "billing-api", name: "Billing API", team: { slug: "payments" } },
    { id: "p3", slug: "emr-bug-triage", name: "EMR bug triage", team: { slug: "clinical" } },
  ],
};

/** Every query still in flight. */
export const DASHBOARD_LOADING: typeof DASHBOARD = {
  ...DASHBOARD,
  teamsCount: undefined,
  teamsLoading: true,
  projectsCount: undefined,
  projectsLoading: true,
  appsCount: 0,
  healthLoading: true,
  deployedCount: 0,
  agentsCount: 0,
  agentFleetLoading: true,
  workflowsCount: 0,
  workflowDefinitionsLoading: true,
  forecast: null,
  costForecastLoading: true,
  inFlightCount: 0,
  activeAgentTasks: [],
  runningAgentTasks: 0,
  queuedAgentTasks: 0,
  activeWorkflowRuns: [],
  recentAgentFailures: 0,
  recentWorkflowFailures: 0,
  runningCount: 0,
  failingCount: 0,
  recentFailureCount: 0,
  noDeployCount: 0,
  recentProjects: [],
};

/** A fresh install: everything readable, nothing created yet. */
export const DASHBOARD_EMPTY: typeof DASHBOARD = {
  ...DASHBOARD_LOADING,
  teamsCount: 0,
  teamsLoading: false,
  projectsCount: 0,
  projectsLoading: false,
  healthLoading: false,
  agentFleetLoading: false,
  workflowDefinitionsLoading: false,
  forecast: { mtdCents: 0, currency: "USD", deltaPct: 0, previousMonthCents: 0 },
  costForecastLoading: false,
};

/** The teams list failed with something other than a 404 empty list. */
export const DASHBOARD_ERROR: typeof DASHBOARD = {
  ...DASHBOARD_EMPTY,
  errorMessage: "Response not successful: Received status code 502",
  teamsCount: undefined,
};

/** Long names everywhere a name is shown. */
export const DASHBOARD_LONG: typeof DASHBOARD = {
  ...DASHBOARD,
  errorMessage: `Upstream ${LONG} did not answer within the 30 second deadline; retry later.`,
  activeAgentTasks: [
    {
      id: `task-${LONG}`,
      agentName: `Agent ${LONG}`,
      agentSlug: LONG,
      projectSlug: LONG,
      status: "provisioning",
    },
  ],
  activeWorkflowRuns: [
    {
      guid: "run-long",
      definitionSlug: LONG,
      definitionName: `Workflow ${LONG}`,
      projectSlug: LONG,
      currentStageOrder: 12,
      currentStageRole: "human-approval-gate-for-regulated-change",
    },
  ],
  recentProjects: [{ id: "p-long", slug: LONG, name: `Project ${LONG}`, team: { slug: LONG } }],
};

const activityItem = (
  id: string,
  action: string,
  targetLabel: string,
  actorDisplay: string,
  minutes: number
): AstroliftActivityItem =>
  ({
    id,
    action,
    actorDisplay,
    eventType: `deploy.${action}`,
    targetKind: "deployment",
    targetLabel,
    targetHref: "#",
    payload: {},
    occurredAt: new Date(Date.UTC(2026, 8, 28, 12, 0) - minutes * 60_000).toISOString(),
  }) as AstroliftActivityItem;

export const ACTIVITY: ActivityFeedProps = {
  items: [
    activityItem("1", "succeeded", "checkout → production", "leo", 2),
    activityItem("2", "failed", "billing-api → staging", "astrolift-bot", 14),
    activityItem("3", "approved", "checkout → production", "eric", 40),
  ],
  loading: false,
  error: null,
  hasMore: true,
  loadingMore: false,
  onLoadMore: () => {},
};
