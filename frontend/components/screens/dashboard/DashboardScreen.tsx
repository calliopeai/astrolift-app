"use client";

import type { ReactNode } from "react";
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

import { KpiTile } from "@/components/KpiTile";
import { ListSummary } from "@/components/list/ListSummary";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAgentTask } from "@/graphql/agents/agents.types";
import type { AstroliftCostForecast } from "@/graphql/billing/billing.types";
import type { AstroliftProject } from "@/graphql/identity/identity.types";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";

/** Canonical KPI tile ids. */
export type KpiTileId =
  | "teams"
  | "projects"
  | "apps"
  | "deployments"
  | "agents"
  | "workflows"
  | "costMtd";

export type DashboardAgentTask = Pick<
  AstroliftAgentTask,
  "id" | "agentName" | "agentSlug" | "projectSlug" | "status"
>;
export type DashboardWorkflowRun = Pick<
  WorkflowDefinitionRun,
  | "guid"
  | "definitionSlug"
  | "definitionName"
  | "projectSlug"
  | "currentStageOrder"
  | "currentStageRole"
>;
export type DashboardProject = Pick<AstroliftProject, "id" | "slug" | "name"> & {
  team: Pick<AstroliftProject["team"], "slug">;
};
export type DashboardCostForecast = Pick<
  AstroliftCostForecast,
  "mtdCents" | "currency" | "deltaPct" | "previousMonthCents"
>;

export interface DashboardScreenProps {
  /** Teams/projects list failure worth a banner (a 404 empty list is not one). */
  errorMessage: string | null;
  /** Visible KPI tiles, in the operator's saved order. */
  tileIds: KpiTileId[];
  onReorderTiles: (activeId: KpiTileId, overId: KpiTileId) => void;

  canViewApps: boolean;
  canViewAgents: boolean;
  canViewWorkflows: boolean;
  canViewProjects: boolean;
  canViewAudit: boolean;

  teamsCount: number | undefined;
  teamsLoading: boolean;
  projectsCount: number | undefined;
  projectsLoading: boolean;
  appsCount: number;
  healthLoading: boolean;
  deployedCount: number;
  agentsCount: number;
  agentFleetLoading: boolean;
  workflowsCount: number;
  workflowDefinitionsLoading: boolean;
  forecast: DashboardCostForecast | null;
  costForecastLoading: boolean;

  inFlightCount: number;
  activeAgentTasks: DashboardAgentTask[];
  runningAgentTasks: number;
  queuedAgentTasks: number;
  activeWorkflowRuns: DashboardWorkflowRun[];
  recentAgentFailures: number;
  recentWorkflowFailures: number;

  runningCount: number;
  failingCount: number;
  recentFailureCount: number;
  noDeployCount: number;

  recentProjects: DashboardProject[];

  /** Onboarding wizard + spotlight tour, mounted around the dashboard. */
  onboarding?: ReactNode;
  /** Recent activity feed, rendered only when the viewer can read the audit log. */
  activity?: ReactNode;
}

export function DashboardScreen({
  errorMessage,
  tileIds,
  onReorderTiles,
  canViewApps,
  canViewAgents,
  canViewWorkflows,
  canViewProjects,
  canViewAudit,
  teamsCount,
  teamsLoading,
  projectsCount,
  projectsLoading,
  appsCount,
  healthLoading,
  deployedCount,
  agentsCount,
  agentFleetLoading,
  workflowsCount,
  workflowDefinitionsLoading,
  forecast,
  costForecastLoading,
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
  onboarding,
  activity,
}: DashboardScreenProps) {
  const t = useTranslations("overview");

  // The distance constraint keeps plain clicks navigating the tile's Link;
  // a drag only starts after 8px of pointer travel.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );
  const handleTileDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      onReorderTiles(active.id as KpiTileId, over.id as KpiTileId);
    }
  };

  const operationalIssueCount =
    failingCount + recentFailureCount + recentAgentFailures + recentWorkflowFailures;
  const hasOperationalModules = canViewApps || canViewAgents || canViewWorkflows;

  // Display value is null until the forecast resolves with a non-zero
  // MTD — a zero MTD with no previous month signal is rendered as the
  // KPI's `emptyCta`, not as a misleading "$0".
  const mtdValue =
    forecast == null
      ? null
      : forecast.mtdCents > 0
        ? formatMoney(forecast.mtdCents, forecast.currency)
        : 0;

  const kpiTiles: Record<KpiTileId, ReactNode> = {
    teams: (
      <KpiTile
        label={t("tiles.teams.label")}
        icon={UsersIcon}
        value={teamsCount}
        loading={teamsLoading}
        href="/administration/teams"
        emptyCta={t("tiles.teams.emptyCta")}
      />
    ),
    projects: (
      <KpiTile
        label={t("tiles.projects.label")}
        icon={FileBoxIcon}
        value={projectsCount}
        loading={projectsLoading}
        href="/administration/projects"
        emptyCta={t("tiles.projects.emptyCta")}
      />
    ),
    apps: (
      <KpiTile
        label={t("tiles.apps.label")}
        icon={RocketIcon}
        value={appsCount}
        loading={healthLoading}
        href="/apps"
        emptyCta={t("tiles.apps.emptyCta")}
      />
    ),
    deployments: (
      <KpiTile
        label={t("tiles.deployments.label")}
        icon={BoxIcon}
        value={healthLoading ? undefined : deployedCount}
        loading={healthLoading}
        href="/deployments"
        emptyCta={t("tiles.deployments.emptyCta")}
      />
    ),
    agents: (
      <KpiTile
        label="Agents"
        icon={BotIcon}
        value={agentsCount}
        loading={agentFleetLoading}
        href="/agents"
        emptyCta="No registered agents"
      />
    ),
    workflows: (
      <KpiTile
        label="Workflows"
        icon={WorkflowIcon}
        value={workflowsCount}
        loading={workflowDefinitionsLoading}
        href="/workflows"
        emptyCta="No repository workflows"
      />
    ),
    costMtd: (
      <KpiTile
        label={t("tiles.costMtd.label")}
        icon={CoinsIcon}
        value={mtdValue}
        loading={costForecastLoading && forecast == null}
        // No href: the Cost console it linked to is gone. The number itself
        // is live, so the tile stays and simply stops being a link.
        emptyCta={t("tiles.costMtd.emptyCta")}
        trend={forecast && forecast.mtdCents > 0 ? <CostMtdDelta forecast={forecast} /> : null}
      />
    ),
  };

  return (
    <PageShell title={t("title")} description={t("description")}>
      {onboarding}
      {errorMessage != null && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
            <div className="flex-1">
              <CardTitle className="text-destructive text-sm">{t("errorBanner.title")}</CardTitle>
              <CardDescription>{errorMessage}</CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleTileDragEnd}
      >
        <SortableContext items={tileIds} strategy={rectSortingStrategy}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
            {tileIds.map((id) => (
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
                <ListSummary<DashboardAgentTask>
                  title="Agents running now"
                  count={activeAgentTasks.length}
                  rows={activeAgentTasks}
                  keyOf={(task) => task.id}
                  rowHref={(task) => `/agents/runs/${encodeURIComponent(task.id)}`}
                  viewAllHref="/agents?tab=active"
                  renderRow={(task) => (
                    <span className="flex min-w-0 items-center justify-between gap-3">
                      <span className="min-w-0">
                        <span className="block truncate font-medium">
                          {task.agentName || task.agentSlug || task.id}
                        </span>
                        <span className="text-muted-foreground block truncate font-mono text-xs">
                          {task.projectSlug || "unassigned"} · {task.id}
                        </span>
                      </span>
                      <Badge variant="secondary" className="shrink-0 capitalize">
                        {task.status}
                      </Badge>
                    </span>
                  )}
                />
              )}
              {canViewWorkflows && activeWorkflowRuns.length > 0 && (
                <ListSummary<DashboardWorkflowRun>
                  title="Workflows running now"
                  count={activeWorkflowRuns.length}
                  rows={activeWorkflowRuns}
                  keyOf={(run) => run.guid}
                  rowHref={(run) => `/workflows/${encodeURIComponent(run.definitionSlug)}/builder`}
                  viewAllHref="/workflows?tab=running"
                  renderRow={(run) => (
                    <span className="flex min-w-0 items-center justify-between gap-3">
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{run.definitionName}</span>
                        <span className="text-muted-foreground block truncate text-xs">
                          {run.projectSlug || "Reusable template"}
                          {run.currentStageOrder != null
                            ? ` · stage ${run.currentStageOrder + 1}${run.currentStageRole ? ` ${run.currentStageRole}` : ""}`
                            : ""}
                        </span>
                      </span>
                      <Badge className="shrink-0">Running</Badge>
                    </span>
                  )}
                />
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
            healthLoading ? (
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
          {healthLoading ? (
            <Skeleton className="h-12 w-full" />
          ) : appsCount === 0 ? (
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
            <ListSummary<DashboardProject>
              className={canViewAudit ? "lg:col-span-2" : "lg:col-span-3"}
              title={t("recentProjects.title")}
              description={t("recentProjects.description")}
              count={projectsLoading ? null : projectsCount}
              rows={recentProjects}
              keyOf={(p) => p.id}
              rowHref={(p) => `/projects/${encodeURIComponent(p.slug)}`}
              viewAllHref="/administration/projects"
              loading={projectsLoading}
              empty={{
                icon: <FileBoxIcon className="size-5" />,
                title: t("recentProjects.emptyTitle"),
                description: t("recentProjects.emptyDescription"),
                actionHref: "/administration/teams",
                actionLabel: t("recentProjects.emptyAction"),
              }}
              renderRow={(p) => (
                <span className="flex min-w-0 items-center justify-between gap-3">
                  <span className="min-w-0">
                    <span className="block truncate font-medium">
                      {p.team.slug}/{p.slug}
                    </span>
                    <span className="text-muted-foreground block truncate text-xs">{p.name}</span>
                  </span>
                  <Badge variant="outline" className="max-w-40 shrink-0 truncate">
                    {p.team.slug}
                  </Badge>
                </span>
              )}
            />
          )}

          {canViewAudit && (
            <Card className={canViewProjects ? undefined : "lg:col-span-3"}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ActivityIcon className="size-4" /> {t("activity.title")}
                </CardTitle>
                <CardDescription>{t("activity.description")}</CardDescription>
              </CardHeader>
              <CardContent>{activity}</CardContent>
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
  children: ReactNode;
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

/**
 * Render the % delta vs previous month under the Cost MTD tile.
 * Up = amber (spending more); down = emerald (spending less); the
 * neutral arrow renders when the previous-month signal is zero so
 * the indicator stays present rather than disappearing.
 */
function CostMtdDelta({ forecast }: { forecast: DashboardCostForecast }) {
  const t = useTranslations("overview");
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
