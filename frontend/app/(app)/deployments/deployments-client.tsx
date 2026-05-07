"use client";

import { useQuery } from "@apollo/client/react";
import { BoxIcon, ClockIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftDeployment,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";

interface Resp {
  astroliftDeployments: AstroliftDeployment[];
}

const statusToDot: Record<DeploymentStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  pending_approval: "warn",
  pending: "warn",
  deploying: "pending",
  redeploying: "pending",
  running: "ok",
  failed: "error",
  superseded: "muted",
  rolled_back: "muted",
};

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

export function DeploymentsClient() {
  const { data, loading } = useQuery<Resp>(LIST_DEPLOYMENTS, {
    variables: { limit: 100 },
    pollInterval: 5000,
  });
  const list = data?.astroliftDeployments ?? [];

  return (
    <PageShell
      title="Deployments"
      description="Every rollout attempt across every app and environment. Click a row to see the workflow timeline, rendered manifests, and logs."
    >
      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title="No deployments yet"
                description="Register an app and roll a deployment from /apps/[slug]/deploy. Deployments land here as soon as the workflow starts."
                actionHref="/apps"
                actionLabel="Open apps"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>App / Env</TableHead>
                  <TableHead>Image</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="w-8">
                      <StatusDot status={statusToDot[d.status]} />
                    </TableCell>
                    <TableCell>
                      <div className="font-medium">{d.registeredAppSlug}</div>
                      <div className="text-muted-foreground text-xs">
                        env <span className="font-mono">{d.environmentName}</span>
                        {d.workloadSlug && (
                          <>
                            {" "}
                            · workload{" "}
                            <span className="font-mono">{d.workloadSlug}</span>
                          </>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {d.imageTag || "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{d.triggerKind}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {d.status.replace(/_/g, " ")}
                      </Badge>
                      {d.approvalsRequired > 0 && (
                        <div className="text-muted-foreground mt-1 text-xs">
                          {d.approvalsReceived}/{d.approvalsRequired} approvals
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      <span className="inline-flex items-center gap-1">
                        <ClockIcon className="size-3" />
                        {formatDuration(d.durationSeconds)}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {d.startedAt
                        ? new Date(d.startedAt).toLocaleString()
                        : new Date(d.createdAt).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
