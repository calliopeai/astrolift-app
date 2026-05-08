"use client";

import { useQuery } from "@apollo/client/react";
import {
  ExternalLinkIcon,
  GitBranchIcon,
  PlusIcon,
  RocketIcon,
} from "lucide-react";
import Link from "next/link";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  ProvisioningStatus,
} from "@/graphql/registry/registry.types";

interface Resp {
  astroliftApps: AstroliftRegisteredApp[];
}

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

export function AppsClient() {
  const { data, loading } = useQuery<Resp>(LIST_APPS);
  const apps = data?.astroliftApps ?? [];

  return (
    <PageShell
      title="Apps"
      description="Repositories registered for deployment. Each app has workloads, environments, deployments, secrets, and managed services attached."
      actions={
        <Can permission="app.create">
          <Button asChild>
            <Link href="/apps/new">
              <PlusIcon className="size-4" />
              Register app
            </Link>
          </Button>
        </Can>
      }
    >
      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : apps.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="p-8">
            <EmptyState
              icon={<RocketIcon className="size-5" />}
              title="No apps registered yet"
              description="Register a Git repository — we parse its astrolift.toml, provision a namespace and registry repo, and roll the first deployment from a Temporal workflow."
              actionHref="/apps/new"
              actionLabel="Register your first app"
            />
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {apps.map((app) => (
            <Link
              key={app.id}
              href={`/apps/${app.slug}`}
              className="contents"
            >
              <Card className="hover:bg-accent/30 group transition-colors">
                <CardContent className="flex flex-col gap-3 p-5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="truncate text-lg font-semibold">{app.name}</h3>
                      <p className="text-muted-foreground font-mono text-xs">
                        {app.teamSlug}/{app.projectSlug}/{app.slug}
                      </p>
                    </div>
                    <StatusDot status={statusDot[app.provisioningStatus]} />
                  </div>

                  {app.description && (
                    <p className="text-muted-foreground line-clamp-2 text-sm">
                      {app.description}
                    </p>
                  )}

                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    {app.sourceRepo && (
                      <Badge variant="outline" className="gap-1">
                        <GitBranchIcon className="size-3" />
                        {app.sourceRepo}
                      </Badge>
                    )}
                    <Badge variant="secondary">{app.sourceKind}</Badge>
                    <Badge variant={app.isActive ? "default" : "secondary"}>
                      {app.provisioningStatus}
                    </Badge>
                  </div>

                  <div className="text-muted-foreground flex items-center justify-between text-xs">
                    <span>
                      branch <span className="font-mono">{app.deployBranch}</span>
                    </span>
                    <span className="group-hover:text-foreground inline-flex items-center gap-1">
                      Open <ExternalLinkIcon className="size-3" />
                    </span>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </PageShell>
  );
}
