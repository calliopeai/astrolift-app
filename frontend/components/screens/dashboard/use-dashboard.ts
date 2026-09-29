"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { ServerError } from "@apollo/client/errors";
import { arrayMove } from "@dnd-kit/sortable";

import { GET_COST_FORECAST } from "@/graphql/billing/billing.queries";
import { LIST_AGENT_FLEET, LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem, AstroliftAgentTask } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import type { AstroliftCostForecast } from "@/graphql/billing/billing.types";
import {
  GET_DEPLOYMENT_METRICS,
  LIST_APP_HEALTH_SUMMARY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppHealthSummary,
  AstroliftDeploymentMetrics,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";
import { useModules } from "@/graphql/user/user.hooks";
import {
  LIST_TIERED_WORKFLOW_DEFINITIONS,
  LIST_WORKFLOW_DEFINITION_RUNS,
} from "@/graphql/workflows/tiered.queries";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { DashboardScreenProps, KpiTileId } from "./DashboardScreen";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}
interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}
interface HealthResp {
  astroliftAppHealthSummary: AstroliftAppHealthSummary[];
}
interface MetricsResp {
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
}
interface CostForecastResp {
  astroliftCostForecast: AstroliftCostForecast;
}
interface AgentFleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface AgentTasksResp {
  agentTasks: AstroliftAgentTask[];
}
interface WorkflowDefinitionsResp {
  workflowDefinitions: WorkflowDefinitionSummary[];
}
interface WorkflowDefinitionRunsResp {
  workflowDefinitionRuns: WorkflowDefinitionRun[];
}

/** Canonical KPI tile ids — also the default left-to-right order. */
const KPI_TILE_IDS: readonly KpiTileId[] = [
  "teams",
  "projects",
  "apps",
  "deployments",
  "agents",
  "workflows",
  "costMtd",
];

const KPI_ORDER_KEY = "astrolift.dashboard.kpiOrder.v1";

/**
 * Reconcile a saved order against the current tile set: unknown ids are
 * dropped, tiles added since the save append in default position. This keeps
 * old saved orders working when tiles are added or removed later.
 */
function reconcileTileOrder(saved: unknown): KpiTileId[] {
  const savedIds = Array.isArray(saved)
    ? saved.filter((id): id is KpiTileId => (KPI_TILE_IDS as readonly string[]).includes(id))
    : [];
  const missing = KPI_TILE_IDS.filter((id) => !savedIds.includes(id));
  return [...savedIds, ...missing];
}

function loadTileOrder(): KpiTileId[] {
  if (typeof window === "undefined") return [...KPI_TILE_IDS];
  try {
    const raw = window.localStorage.getItem(KPI_ORDER_KEY);
    if (!raw) return [...KPI_TILE_IDS];
    return reconcileTileOrder(JSON.parse(raw));
  } catch {
    return [...KPI_TILE_IDS];
  }
}

function saveTileOrder(order: KpiTileId[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KPI_ORDER_KEY, JSON.stringify(order));
  } catch {
    // localStorage might be disabled — degrade silently.
  }
}

// A 404 on a list query means the resource collection is empty
// for this install — surface it as the empty state, not a banner.
// Anything else (5xx, GraphQL errors, network drop) still escalates.
const isEmptyListError = (err: Error | undefined): boolean =>
  err != null && ServerError.is(err) && err.statusCode === 404;

/**
 * Everything the overview dashboard reads: role-scoped queries (each skipped
 * when the viewer's modules/permissions exclude it), the derived fleet and
 * operational counts, and the per-operator KPI tile order.
 */
export function useDashboard(): Omit<DashboardScreenProps, "onboarding" | "activity"> {
  const modules = useModules();
  const permissions = useMyPermissions();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const canViewApps = modules.canView("apps");
  const canViewAgents = modules.canView("agents");
  const canViewWorkflows = modules.canView("workflows");
  const canViewTeams = permissions.can("team.read");
  const canViewProjects = permissions.can("project.read");
  const canViewBilling = permissions.can("billing.read");
  const canViewAudit = permissions.can("audit_log.read");

  const teams = useQuery<TeamsResp>(LIST_TEAMS, { skip: permissions.loading || !canViewTeams });
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS, {
    skip: permissions.loading || !canViewProjects,
  });
  const health = useQuery<HealthResp>(LIST_APP_HEALTH_SUMMARY, {
    skip: modules.loading || !canViewApps,
  });
  const metrics = useQuery<MetricsResp>(GET_DEPLOYMENT_METRICS, {
    variables: { windowDays: 30 },
    skip: modules.loading || !canViewApps,
  });
  // Cost MTD pulls from the live aggregator (#432) — no client-side
  // estimation, all numbers come from the cloud's billing API via
  // CostSnapshot rows. fetchPolicy keeps the headline fresh without
  // gating the rest of the dashboard.
  const costForecast = useQuery<CostForecastResp>(GET_COST_FORECAST, {
    fetchPolicy: "cache-and-network",
    skip: permissions.loading || !canViewBilling,
  });
  const agentFleet = useQuery<AgentFleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId || modules.loading || !canViewAgents,
    fetchPolicy: "cache-and-network",
  });
  const agentTasks = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null, workloadId: null },
    skip: !orgId || modules.loading || !canViewAgents,
    fetchPolicy: "cache-and-network",
    pollInterval: 10_000,
  });
  const workflowDefinitions = useQuery<WorkflowDefinitionsResp>(LIST_TIERED_WORKFLOW_DEFINITIONS, {
    variables: { orgId: orgId || null, projectId: null },
    skip: !orgId || modules.loading || !canViewWorkflows,
    fetchPolicy: "cache-and-network",
  });
  const workflowRuns = useQuery<WorkflowDefinitionRunsResp>(LIST_WORKFLOW_DEFINITION_RUNS, {
    variables: { orgId: orgId || null, projectId: null, status: null, limit: 100 },
    skip: !orgId || modules.loading || !canViewWorkflows,
    fetchPolicy: "cache-and-network",
    pollInterval: 10_000,
  });
  const [dashboardNow, setDashboardNow] = useState(0);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setDashboardNow(Date.now()));
    const interval = window.setInterval(() => setDashboardNow(Date.now()), 60_000);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearInterval(interval);
    };
  }, []);

  // Tile order is per-operator, persisted in localStorage (#1056a). SSR and
  // first client paint use the default order; the saved order applies after
  // mount so hydration stays consistent.
  const [tileOrder, setTileOrder] = useState<KpiTileId[]>([...KPI_TILE_IDS]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setTileOrder(loadTileOrder()));
    return () => window.cancelAnimationFrame(frame);
  }, []);
  const onReorderTiles = (activeId: KpiTileId, overId: KpiTileId) => {
    setTileOrder((ids) => {
      const next = arrayMove(ids, ids.indexOf(activeId), ids.indexOf(overId));
      saveTileOrder(next);
      return next;
    });
  };

  const teamsCount = isEmptyListError(teams.error) ? 0 : teams.data?.astroliftTeams.length;
  const projectsCount = isEmptyListError(projects.error)
    ? 0
    : projects.data?.astroliftProjects.length;

  const apps = (health.data?.astroliftAppHealthSummary ?? []).filter(
    (app) => app.primitiveKind !== "agent"
  );
  const agents = agentFleet.data?.agentFleet ?? [];
  const agentTaskRows = agentTasks.data?.agentTasks ?? [];
  const runnableWorkflows = (workflowDefinitions.data?.workflowDefinitions ?? []).filter(
    (workflow) =>
      !workflow.isGlobal && Boolean(workflow.sourceRepo) && Boolean(workflow.projectGuid)
  );
  const workflowRunRows = workflowRuns.data?.workflowDefinitionRuns ?? [];
  const activeAgentTasks = agentTaskRows.filter((task) =>
    ["queued", "provisioning", "running"].includes(task.status)
  );
  const runningAgentTasks = activeAgentTasks.filter((task) => task.status === "running").length;
  const queuedAgentTasks = activeAgentTasks.length - runningAgentTasks;
  const activeWorkflowRuns = workflowRunRows.filter((run) => run.status === "running");
  const recentCutoff = dashboardNow - 24 * 60 * 60 * 1000;
  const recentAgentFailures = agentTaskRows.filter(
    (task) =>
      dashboardNow > 0 &&
      ["failed", "timed_out"].includes(task.status) &&
      Date.parse(task.finishedAt ?? task.createdAt) >= recentCutoff
  ).length;
  const recentWorkflowFailures = workflowRunRows.filter(
    (run) =>
      dashboardNow > 0 &&
      ["failed", "timed_out"].includes(run.status) &&
      Date.parse(run.endedAt ?? run.startedAt ?? "") >= recentCutoff
  ).length;
  // Composite health badge: an app counts as healthy when its latest
  // deploy status is 'running' AND there's been no terminal failure
  // in the recent window. ``recentFailureCount`` deliberately scopes
  // to apps that are currently still 'running' so we surface "ran
  // but had a hiccup" — distinct from "currently broken".
  const runningCount = apps.filter(
    (a) => a.latestDeploymentStatus === "running" && !a.hasRecentFailure
  ).length;
  const failingCount = apps.filter(
    (a) => a.latestDeploymentStatus === "failed" || a.latestDeploymentStatus === "rolled_back"
  ).length;
  const recentFailureCount = apps.filter(
    (a) => a.hasRecentFailure && a.latestDeploymentStatus === "running"
  ).length;
  const noDeployCount = apps.filter((a) => a.latestDeploymentStatus == null).length;
  // "Running apps" tile: count apps whose latest deployment reached RUNNING
  // (includes apps that had a recent hiccup but are currently up — those
  // apps ARE running even if they had a failure in the recent window).
  // inFlight is preserved for the deployment-metrics chart below.
  const deployedCount = apps.filter((a) => a.latestDeploymentStatus === "running").length;
  const inFlightCount = metrics.data?.astroliftDeploymentMetrics.inFlight ?? 0;
  const tileIds = tileOrder.filter((id) => {
    if (id === "teams") return canViewTeams;
    if (id === "projects") return canViewProjects;
    if (id === "apps" || id === "deployments") return canViewApps;
    if (id === "agents") return canViewAgents;
    if (id === "workflows") return canViewWorkflows;
    return canViewBilling;
  });

  const recentProjects = projects.data?.astroliftProjects.slice(0, 5) ?? [];
  const teamsBannerError = isEmptyListError(teams.error) ? null : teams.error;
  const projectsBannerError = isEmptyListError(projects.error) ? null : projects.error;
  const errorBanner = teamsBannerError ?? projectsBannerError ?? null;

  return {
    errorMessage: errorBanner ? errorBanner.message : null,
    tileIds,
    onReorderTiles,
    canViewApps,
    canViewAgents,
    canViewWorkflows,
    canViewProjects,
    canViewAudit,
    teamsCount,
    teamsLoading: teams.loading,
    projectsCount,
    projectsLoading: projects.loading,
    appsCount: apps.length,
    healthLoading: health.loading,
    deployedCount,
    agentsCount: agents.length,
    agentFleetLoading: agentFleet.loading,
    workflowsCount: runnableWorkflows.length,
    workflowDefinitionsLoading: workflowDefinitions.loading,
    forecast: costForecast.data?.astroliftCostForecast ?? null,
    costForecastLoading: costForecast.loading,
    inFlightCount,
    activeAgentTasks,
    runningAgentTasks,
    queuedAgentTasks,
    activeWorkflowRuns,
    recentAgentFailures,
    recentWorkflowFailures,
    runningCount,
    failingCount,
    recentFailureCount,
    noDeployCount,
    recentProjects,
  };
}
