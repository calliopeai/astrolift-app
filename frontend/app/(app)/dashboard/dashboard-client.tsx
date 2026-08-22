"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { ServerError } from "@apollo/client/errors";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  rectSortingStrategy,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowRightIcon,
  ArrowUpIcon,
  BotIcon,
  BoxIcon,
  CheckCircle2Icon,
  CircleXIcon,
  CoinsIcon,
  FileBoxIcon,
  GripVerticalIcon,
  HeartPulseIcon,
  RocketIcon,
  UsersIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { ActivityFeed } from "@/components/ActivityFeed";
import { EmptyState } from "@/components/EmptyState";
import { KpiTile } from "@/components/KpiTile";
import { OnboardingHost } from "@/components/onboarding/OnboardingHost";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
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
const KPI_TILE_IDS = [
  "teams",
  "projects",
  "apps",
  "deployments",
  "agents",
  "workflows",
  "costMtd",
] as const;
type KpiTileId = (typeof KPI_TILE_IDS)[number];

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

export function DashboardClient() {
  const t = useTranslations("overview");
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
  // The distance constraint keeps plain clicks navigating the tile's Link;
  // a drag only starts after 8px of pointer travel.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );
  const handleTileDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      setTileOrder((ids) => {
        const oldIndex = ids.indexOf(active.id as KpiTileId);
        const newIndex = ids.indexOf(over.id as KpiTileId);
        const next = arrayMove(ids, oldIndex, newIndex);
        saveTileOrder(next);
        return next;
      });
    }
  };

  // A 404 on a list query means the resource collection is empty
  // for this install — surface it as the empty state, not a banner.
  // Anything else (5xx, GraphQL errors, network drop) still escalates.
  const isEmptyListError = (err: Error | undefined): boolean =>
    err != null && ServerError.is(err) && err.statusCode === 404;
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
  const operationalIssueCount =
    failingCount + recentFailureCount + recentAgentFailures + recentWorkflowFailures;
  const hasOperationalModules = canViewApps || canViewAgents || canViewWorkflows;
  const visibleTileIds = tileOrder.filter((id) => {
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

  const forecast = costForecast.data?.astroliftCostForecast ?? null;
  // Display value is null until the forecast resolves with a non-zero
  // MTD — a zero MTD with no previous month signal is rendered as the
  // KPI's `emptyCta`, not as a misleading "$0".
  const mtdValue =
    forecast == null
      ? null
      : forecast.mtdCents > 0
        ? formatMoney(forecast.mtdCents, forecast.currency)
        : 0;

  const kpiTiles: Record<KpiTileId, React.ReactNode> = {
    teams: (
      <KpiTile
        label={t("tiles.teams.label")}
        icon={UsersIcon}
        value={teamsCount}
        loading={teams.loading}
        href="/administration/teams"
        emptyCta={t("tiles.teams.emptyCta")}
      />
    ),
    projects: (
      <KpiTile
        label={t("tiles.projects.label")}
        icon={FileBoxIcon}
        value={projectsCount}
        loading={projects.loading}
        href="/administration/projects"
        emptyCta={t("tiles.projects.emptyCta")}
      />
    ),
    apps: (
      <KpiTile
        label={t("tiles.apps.label")}
        icon={RocketIcon}
        value={apps.length}
        loading={health.loading}
        href="/apps"
        emptyCta={t("tiles.apps.emptyCta")}
      />
    ),
    deployments: (
      <KpiTile
        label={t("tiles.deployments.label")}
        icon={BoxIcon}
        value={health.loading ? undefined : deployedCount}
        loading={health.loading}
        href="/deployments"
        emptyCta={t("tiles.deployments.emptyCta")}
      />
    ),
    agents: (
      <KpiTile
        label="Agents"
        icon={BotIcon}
        value={agents.length}
        loading={agentFleet.loading}
        href="/agents"
        emptyCta="No registered agents"
      />
    ),
    workflows: (
      <KpiTile
        label="Workflows"
        icon={WorkflowIcon}
        value={runnableWorkflows.length}
        loading={workflowDefinitions.loading}
        href="/workflows"
        emptyCta="No repository workflows"
      />
    ),
    costMtd: (
      <KpiTile
        label={t("tiles.costMtd.label")}
        icon={CoinsIcon}
        value={mtdValue}
        loading={costForecast.loading && forecast == null}
        // No href: the Cost console it linked to is gone. The number itself
        // is live, so the tile stays and simply stops being a link.
        emptyCta={t("tiles.costMtd.emptyCta")}
        trend={
          forecast && forecast.mtdCents > 0 ? <CostMtdDelta forecast={forecast} t={t} /> : null
        }
      />
    ),
  };

  return (
    <PageShell title={t("title")} description={t("description")}>
      <OnboardingHost />
      {errorBanner && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
            <div className="flex-1">
              <CardTitle className="text-destructive text-sm">{t("errorBanner.title")}</CardTitle>
              <CardDescription>{errorBanner.message}</CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleTileDragEnd}
      >
        <SortableContext items={visibleTileIds} strategy={rectSortingStrategy}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
            {visibleTileIds.map((id) => (
              <SortableKpiTile key={id} id={id} tourId={id === "apps" ? "apps-tile" : undefined}>
                {kpiTiles[id]}
              </SortableKpiTile>
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {hasOperationalModules && (
        <Section
          title={
            <span className="flex items-center gap-2">
              <ActivityIcon className="size-4" /> Operational state
            </span>
          }
          description="Live state from the apps, agents, and workflows your current role can read."
          action={
            operationalIssueCount > 0 ? (
              <Badge className="bg-warning/15 text-warning-fg gap-1">
                <AlertTriangleIcon className="size-3" /> {operationalIssueCount} need attention
              </Badge>
            ) : (
              <Badge className="bg-success/15 text-success-fg gap-1">
                <CheckCircle2Icon className="size-3" /> No current alerts
              </Badge>
            )
          }
        >
          <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            {canViewApps && (
              <FleetTile
                label="Deployments in flight"
                value={inFlightCount}
                tone={inFlightCount > 0 ? "pending" : "muted"}
                href="/deployments"
              />
            )}
            {canViewAgents && (
              <FleetTile
                label="Active agent tasks"
                value={activeAgentTasks.length}
                detail={
                  activeAgentTasks.length > 0
                    ? `${runningAgentTasks} running · ${queuedAgentTasks} queued or provisioning`
                    : undefined
                }
                tone={activeAgentTasks.length > 0 ? "pending" : "muted"}
                href="/agents"
              />
            )}
            {canViewWorkflows && (
              <FleetTile
                label="Active workflow runs"
                value={activeWorkflowRuns.length}
                tone={activeWorkflowRuns.length > 0 ? "pending" : "muted"}
                href="/workflows?tab=running"
              />
            )}
            <FleetTile
              label="Failures in 24h"
              value={recentAgentFailures + recentWorkflowFailures + recentFailureCount}
              tone={
                recentAgentFailures + recentWorkflowFailures + recentFailureCount > 0
                  ? "error"
                  : "ok"
              }
              href={
                canViewAgents
                  ? "/agents?tab=history"
                  : canViewWorkflows
                    ? "/workflows?tab=history"
                    : "/deployments"
              }
            />
          </div>

          {(activeAgentTasks.length > 0 || activeWorkflowRuns.length > 0) && (
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              {canViewAgents && activeAgentTasks.length > 0 && (
                <div className="rounded-lg border">
                  <div className="border-b px-4 py-3">
                    <h3 className="font-medium">Agents running now</h3>
                  </div>
                  <ul className="divide-y">
                    {activeAgentTasks.slice(0, 5).map((task) => (
                      <li
                        key={task.id}
                        className="flex items-center justify-between gap-3 px-4 py-3"
                      >
                        <div className="min-w-0">
                          <Link
                            href={`/agents/runs/${encodeURIComponent(task.id)}`}
                            className="truncate font-medium hover:underline"
                          >
                            {task.agentName || task.agentSlug || task.id}
                          </Link>
                          <p className="text-muted-foreground truncate font-mono text-xs">
                            {task.projectSlug || "unassigned"} · {task.id}
                          </p>
                        </div>
                        <Badge variant="secondary" className="capitalize">
                          {task.status}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {canViewWorkflows && activeWorkflowRuns.length > 0 && (
                <div className="rounded-lg border">
                  <div className="border-b px-4 py-3">
                    <h3 className="font-medium">Workflows running now</h3>
                  </div>
                  <ul className="divide-y">
                    {activeWorkflowRuns.slice(0, 5).map((run) => (
                      <li
                        key={run.guid}
                        className="flex items-center justify-between gap-3 px-4 py-3"
                      >
                        <div className="min-w-0">
                          <Link
                            href={`/workflows/${encodeURIComponent(run.definitionSlug)}/builder`}
                            className="truncate font-medium hover:underline"
                          >
                            {run.definitionName}
                          </Link>
                          <p className="text-muted-foreground truncate text-xs">
                            {run.projectSlug || "Reusable template"}
                            {run.currentStageOrder != null
                              ? ` · stage ${run.currentStageOrder + 1}${run.currentStageRole ? ` ${run.currentStageRole}` : ""}`
                              : ""}
                          </p>
                        </div>
                        <Badge>Running</Badge>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Section>
      )}

      {canViewApps && (
        <Section
          title={
            <span className="flex items-center gap-2">
              <HeartPulseIcon className="size-4" /> {t("fleetHealth.title")}
            </span>
          }
          description={t("fleetHealth.description")}
          action={
            health.loading ? (
              <Skeleton className="h-6 w-20" />
            ) : failingCount === 0 && recentFailureCount === 0 ? (
              <Badge className="bg-success/15 text-success-fg gap-1">
                <CheckCircle2Icon className="size-3" /> {t("fleetHealth.healthy")}
              </Badge>
            ) : failingCount > 0 ? (
              <Badge variant="destructive" className="bg-danger/15 text-danger-fg gap-1">
                <CircleXIcon className="size-3" />
                {t("fleetHealth.failing", { count: failingCount })}
              </Badge>
            ) : (
              <Badge className="bg-warning/15 text-warning-fg gap-1">
                <AlertTriangleIcon className="size-3" />
                {t("fleetHealth.hiccup", { count: recentFailureCount })}
              </Badge>
            )
          }
        >
          {health.loading ? (
            <Skeleton className="h-12 w-full" />
          ) : apps.length === 0 ? (
            <p className="text-muted-foreground text-sm">{t("fleetHealth.empty")}</p>
          ) : (
            <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
              <FleetTile
                label={t("fleetHealth.tiles.running")}
                value={runningCount}
                tone="ok"
                href="/deployments"
              />
              <FleetTile
                label={t("fleetHealth.tiles.recentFailure")}
                value={recentFailureCount}
                tone="warn"
                href="/deployments"
              />
              <FleetTile
                label={t("fleetHealth.tiles.failing")}
                value={failingCount}
                tone="error"
                href="/deployments"
              />
              <FleetTile
                label={t("fleetHealth.tiles.noDeploys")}
                value={noDeployCount}
                tone="muted"
                href="/apps"
              />
            </div>
          )}
        </Section>
      )}

      {(canViewProjects || canViewAudit) && (
        <div className="grid gap-4 lg:grid-cols-3">
          {canViewProjects && (
            <Card className={canViewAudit ? "lg:col-span-2" : "lg:col-span-3"}>
              <CardHeader>
                <CardTitle>{t("recentProjects.title")}</CardTitle>
                <CardDescription>{t("recentProjects.description")}</CardDescription>
              </CardHeader>
              <CardContent>
                {projects.loading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-10 w-full" />
                    <Skeleton className="h-10 w-full" />
                    <Skeleton className="h-10 w-full" />
                  </div>
                ) : recentProjects.length === 0 ? (
                  <EmptyState
                    icon={<FileBoxIcon className="size-5" />}
                    title={t("recentProjects.emptyTitle")}
                    description={t("recentProjects.emptyDescription")}
                    actionHref="/administration/teams"
                    actionLabel={t("recentProjects.emptyAction")}
                  />
                ) : (
                  <ul className="divide-y">
                    {recentProjects.map((p) => (
                      <li key={p.id} className="flex items-center justify-between py-3">
                        <div>
                          <Link
                            href={`/projects/${encodeURIComponent(p.slug)}`}
                            className="font-medium hover:underline"
                          >
                            {p.team.slug}/{p.slug}
                          </Link>
                          <p className="text-muted-foreground text-xs">{p.name}</p>
                        </div>
                        <Badge variant="outline">{p.team.slug}</Badge>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          )}

          {canViewAudit && (
            <Card className={canViewProjects ? undefined : "lg:col-span-3"}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ActivityIcon className="size-4" /> {t("activity.title")}
                </CardTitle>
                <CardDescription>{t("activity.description")}</CardDescription>
              </CardHeader>
              <CardContent>
                <ActivityFeed />
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </PageShell>
  );
}

/**
 * Sortable wrapper for one KPI tile (#1056a). A dedicated grip button is the
 * drag handle (activator node), so the inner Link keeps normal click and
 * keyboard activation while the grip is keyboard-reorderable (space to lift,
 * arrows to move, space to drop). The corner dot is the #1055 hero accent,
 * kept as a static ornament.
 */
function SortableKpiTile({
  id,
  tourId,
  children,
}: {
  id: KpiTileId;
  tourId?: string;
  children: React.ReactNode;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });
  const style = { transform: CSS.Transform.toString(transform), transition };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`group relative ${isDragging ? "z-10 opacity-60" : ""}`}
      data-onboarding-tour={tourId}
    >
      <span
        aria-hidden
        className="bg-primary/70 pointer-events-none absolute top-2 right-2 size-1.5 rounded-full"
      />
      <button
        type="button"
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-label="Reorder tile"
        className="text-muted-foreground absolute top-1 right-4 cursor-grab opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
      >
        <GripVerticalIcon className="size-3.5" />
      </button>
      {children}
    </div>
  );
}

const FLEET_TONE: Record<"ok" | "warn" | "error" | "pending" | "muted", string> = {
  ok: "border-success-border bg-success/5",
  warn: "border-warning-border bg-warning/5",
  error: "border-danger-border bg-danger/5",
  pending: "border-primary/30 bg-primary/5",
  muted: "border-muted bg-muted/20",
};

function FleetTile({
  label,
  value,
  tone,
  href,
  detail,
}: {
  label: string;
  value: number;
  tone: "ok" | "warn" | "error" | "pending" | "muted";
  href: string;
  detail?: string;
}) {
  return (
    <Link
      href={href}
      className={`group rounded-md border ${FLEET_TONE[tone]} hover:bg-accent/30 p-3 transition-colors`}
    >
      <div className="text-muted-foreground text-xs tracking-wide uppercase">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value}</div>
      {detail && <div className="text-muted-foreground mt-1 text-xs">{detail}</div>}
    </Link>
  );
}

interface CostMtdDeltaProps {
  forecast: AstroliftCostForecast;
  t: ReturnType<typeof useTranslations<"overview">>;
}

/**
 * Render the % delta vs previous month under the Cost MTD tile.
 * Up = amber (spending more); down = emerald (spending less); the
 * neutral arrow renders when the previous-month signal is zero so
 * the indicator stays present rather than disappearing.
 */
function CostMtdDelta({ forecast, t }: CostMtdDeltaProps) {
  const delta = forecast.deltaPct;
  const Direction = delta > 0 ? ArrowUpIcon : delta < 0 ? ArrowDownIcon : ArrowRightIcon;
  const tone =
    delta > 0 ? "text-warning-fg" : delta < 0 ? "text-success-fg" : "text-muted-foreground";
  // Zero previous-month spend → no comparable signal; render the
  // neutral label rather than "0.0% vs prev month".
  if (forecast.previousMonthCents === 0) {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
        <ArrowRightIcon className="size-3" />
        {t("tiles.costMtd.noPrevMonth")}
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center gap-1 text-xs ${tone}`}>
      <Direction className="size-3" />
      <span className="font-mono">
        {Math.abs(delta).toFixed(1)}% {t("tiles.costMtd.vsPrevMonth")}
      </span>
    </span>
  );
}

function formatMoney(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(cents / 100);
}
