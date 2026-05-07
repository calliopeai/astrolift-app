"use client";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BoxIcon,
  FileBoxIcon,
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

export function DashboardClient() {
  const teams = useQuery<TeamsResp>(LIST_TEAMS);
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS);

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
      value: 0, // wired once the RegisteredApp surface lands
      loading: false,
      href: "/apps",
    },
    {
      label: "Active deployments",
      icon: BoxIcon,
      value: 0, // wired once the Deployment surface lands
      loading: false,
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
