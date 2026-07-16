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
  BoxIcon,
  CheckCircle2Icon,
  CircleXIcon,
  CoinsIcon,
  FileBoxIcon,
  GripVerticalIcon,
  HeartPulseIcon,
  RocketIcon,
  UsersIcon,
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

/** Canonical KPI tile ids — also the default left-to-right order. */
const KPI_TILE_IDS = ["teams", "projects", "apps", "deployments", "costMtd"] as const;
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
  const teams = useQuery<TeamsResp>(LIST_TEAMS);
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS);
  const health = useQuery<HealthResp>(LIST_APP_HEALTH_SUMMARY);
  const metrics = useQuery<MetricsResp>(GET_DEPLOYMENT_METRICS, {
    variables: { windowDays: 30 },
  });
  // Cost MTD pulls from the live aggregator (#432) — no client-side
  // estimation, all numbers come from the cloud's billing API via
  // CostSnapshot rows. fetchPolicy keeps the headline fresh without
  // gating the rest of the dashboard.
  const costForecast = useQuery<CostForecastResp>(GET_COST_FORECAST, {
    fetchPolicy: "cache-and-network",
  });

  // Tile order is per-operator, persisted in localStorage (#1056a). SSR and
  // first client paint use the default order; the saved order applies after
  // mount so hydration stays consistent.
  const [tileOrder, setTileOrder] = useState<KpiTileId[]>([...KPI_TILE_IDS]);
  useEffect(() => {
    setTileOrder(loadTileOrder());
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

  const apps = health.data?.astroliftAppHealthSummary ?? [];
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
  const deployedCount = apps.filter(
    (a) => a.latestDeploymentStatus === "running"
  ).length;
  const inFlightCount = metrics.data?.astroliftDeploymentMetrics.inFlight ?? 0;

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
    costMtd: (
      <KpiTile
        label={t("tiles.costMtd.label")}
        icon={CoinsIcon}
        value={mtdValue}
        loading={costForecast.loading && forecast == null}
        href="/administration/cost"
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

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleTileDragEnd}>
        <SortableContext items={tileOrder} strategy={rectSortingStrategy}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {tileOrder.map((id) => (
              <SortableKpiTile key={id} id={id} tourId={id === "apps" ? "apps-tile" : undefined}>
                {kpiTiles[id]}
              </SortableKpiTile>
            ))}
          </div>
        </SortableContext>
      </DndContext>

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
            <Badge className="gap-1 bg-success/15 text-success-fg">
              <CheckCircle2Icon className="size-3" /> {t("fleetHealth.healthy")}
            </Badge>
          ) : failingCount > 0 ? (
            <Badge variant="destructive" className="gap-1 bg-danger/15 text-danger-fg">
              <CircleXIcon className="size-3" />
              {t("fleetHealth.failing", { count: failingCount })}
            </Badge>
          ) : (
            <Badge className="gap-1 bg-warning/15 text-warning-fg">
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

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
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
                      <Link href="/administration/projects" className="font-medium hover:underline">
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

        <Card>
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
      </div>

      <Card data-onboarding-tour="apps-nav">
        <CardHeader>
          <CardTitle>{t("registeredApps.title")}</CardTitle>
          <CardDescription>{t("registeredApps.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState
            icon={<BoxIcon className="size-5" />}
            title={t("registeredApps.emptyTitle")}
            description={t("registeredApps.emptyDescription")}
            actionHref="/apps"
            actionLabel={t("registeredApps.emptyAction")}
          />
        </CardContent>
      </Card>
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

const FLEET_TONE: Record<"ok" | "warn" | "error" | "muted", string> = {
  ok: "border-success-border bg-success/5",
  warn: "border-warning-border bg-warning/5",
  error: "border-danger-border bg-danger/5",
  muted: "border-muted bg-muted/20",
};

function FleetTile({
  label,
  value,
  tone,
  href,
}: {
  label: string;
  value: number;
  tone: "ok" | "warn" | "error" | "muted";
  href: string;
}) {
  return (
    <Link
      href={href}
      className={`group rounded-md border ${FLEET_TONE[tone]} hover:bg-accent/30 p-3 transition-colors`}
    >
      <div className="text-muted-foreground text-xs tracking-wide uppercase">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value}</div>
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
    delta > 0
      ? "text-warning-fg"
      : delta < 0
        ? "text-success-fg"
        : "text-muted-foreground";
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
