"use client";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BoxIcon,
  CheckCircle2Icon,
  CircleXIcon,
  FileBoxIcon,
  HeartPulseIcon,
  RocketIcon,
  UsersIcon,
} from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  GET_DEPLOYMENT_METRICS,
  LIST_APP_HEALTH_SUMMARY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppHealthSummary,
  AstroliftDeploymentMetrics,
} from "@/graphql/lifecycle/lifecycle.types";
import {
  LIST_PROJECTS,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftProject,
  AstroliftTeam,
} from "@/graphql/identity/identity.types";

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

export function DashboardClient() {
  const teams = useQuery<TeamsResp>(LIST_TEAMS);
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS);
  const health = useQuery<HealthResp>(LIST_APP_HEALTH_SUMMARY);
  const metrics = useQuery<MetricsResp>(GET_DEPLOYMENT_METRICS, {
    variables: { windowDays: 30 },
  });

  const apps = health.data?.astroliftAppHealthSummary ?? [];
  // Composite health badge: an app counts as healthy when its latest
  // deploy status is 'running' AND there's been no terminal failure
  // in the recent window. ``recentFailureCount`` deliberately scopes
  // to apps that are currently still 'running' so we surface "ran
  // but had a hiccup" — distinct from "currently broken".
  const runningCount = apps.filter(
    (a) => a.latestDeploymentStatus === "running" && !a.hasRecentFailure,
  ).length;
  const failingCount = apps.filter(
    (a) =>
      a.latestDeploymentStatus === "failed" ||
      a.latestDeploymentStatus === "rolled_back",
  ).length;
  const recentFailureCount = apps.filter(
    (a) => a.hasRecentFailure && a.latestDeploymentStatus === "running",
  ).length;
  const noDeployCount = apps.filter(
    (a) => a.latestDeploymentStatus == null,
  ).length;
  const inFlightCount =
    metrics.data?.astroliftDeploymentMetrics.inFlight ?? 0;

  const tiles = [
    {
      label: "Teams",
      icon: UsersIcon,
      value: teams.data?.astroliftTeams.length,
      loading: teams.loading,
      href: "/teams",
    },
    {
      label: "Projects",
      icon: FileBoxIcon,
      value: projects.data?.astroliftProjects.length,
      loading: projects.loading,
      href: "/projects",
    },
    {
      label: "Registered apps",
      icon: RocketIcon,
      value: apps.length,
      loading: health.loading,
      href: "/apps",
    },
    {
      label: "Active deployments",
      icon: BoxIcon,
      value: inFlightCount,
      loading: metrics.loading,
      href: "/deployments",
    },
  ];

  const recentProjects = projects.data?.astroliftProjects.slice(0, 5) ?? [];
  const errorBanner = teams.error ?? projects.error ?? null;

  return (
    <PageShell
      title="Overview"
      description="Identity, registry, and lifecycle data for this Astrolift install."
    >
      {errorBanner && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
            <div className="flex-1">
              <CardTitle className="text-destructive text-sm">
                Couldn&apos;t load some data
              </CardTitle>
              <CardDescription>{errorBanner.message}</CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map(({ label, icon: Icon, value, loading, href }) => (
          <Link key={label} href={href} className="contents">
            <Card className="hover:bg-accent/40 transition-colors">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-muted-foreground text-sm font-medium">
                  {label}
                </CardTitle>
                <div className="bg-primary/10 text-primary rounded-md p-1.5">
                  <Icon className="h-4 w-4" />
                </div>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <Skeleton className="h-8 w-16" />
                ) : (
                  <p className="text-2xl font-bold tabular-nums">{value ?? "—"}</p>
                )}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <HeartPulseIcon className="size-4" /> Fleet health
            </CardTitle>
            <CardDescription>
              Composite of latest deployment status across registered apps.
            </CardDescription>
          </div>
          {health.loading ? (
            <Skeleton className="h-6 w-20" />
          ) : failingCount === 0 && recentFailureCount === 0 ? (
            <Badge className="gap-1 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
              <CheckCircle2Icon className="size-3" /> healthy
            </Badge>
          ) : failingCount > 0 ? (
            <Badge
              variant="destructive"
              className="gap-1 bg-red-500/15 text-red-700 dark:text-red-300"
            >
              <CircleXIcon className="size-3" />
              {failingCount} failing
            </Badge>
          ) : (
            <Badge className="gap-1 bg-amber-500/15 text-amber-700 dark:text-amber-300">
              <AlertTriangleIcon className="size-3" />
              {recentFailureCount} hiccup
              {recentFailureCount === 1 ? "" : "s"}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {health.loading ? (
            <Skeleton className="h-12 w-full" />
          ) : apps.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No registered apps yet. Connect a repo on /apps/new to start.
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
              <FleetTile
                label="Running"
                value={runningCount}
                tone="ok"
                href="/deployments"
              />
              <FleetTile
                label="Recent failure"
                value={recentFailureCount}
                tone="warn"
                href="/deployments"
              />
              <FleetTile
                label="Failing"
                value={failingCount}
                tone="error"
                href="/deployments"
              />
              <FleetTile
                label="No deploys"
                value={noDeployCount}
                tone="muted"
                href="/apps"
              />
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Recent projects</CardTitle>
            <CardDescription>
              Most recently created projects across all teams.
            </CardDescription>
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
                title="No projects yet"
                description="Create a team and a project to start registering apps."
                actionHref="/teams"
                actionLabel="Manage teams"
              />
            ) : (
              <ul className="divide-y">
                {recentProjects.map((p) => (
                  <li
                    key={p.id}
                    className="flex items-center justify-between py-3"
                  >
                    <div>
                      <Link href="/projects" className="font-medium hover:underline">
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
            <CardTitle>Activity</CardTitle>
            <CardDescription>Cluster-wide event stream (coming soon).</CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState
              icon={<ActivityIcon className="size-5" />}
              title="Event log empty"
              description="Events appear here as deployments and platform changes happen."
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Registered apps</CardTitle>
          <CardDescription>Repositories registered for deployment.</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState
            icon={<BoxIcon className="size-5" />}
            title="No apps registered"
            description="The app registry surface lands in the next milestone — once it's wired you can register a Git repo and roll a deployment from this card."
            actionHref="/apps"
            actionLabel="Open registry"
          />
        </CardContent>
      </Card>
    </PageShell>
  );
}

const FLEET_TONE: Record<
  "ok" | "warn" | "error" | "muted",
  string
> = {
  ok: "border-emerald-500/30 bg-emerald-500/5",
  warn: "border-amber-500/30 bg-amber-500/5",
  error: "border-red-500/30 bg-red-500/5",
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
      className={`group rounded-md border ${FLEET_TONE[tone]} p-3 transition-colors hover:bg-accent/30`}
    >
      <div className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value}</div>
    </Link>
  );
}
