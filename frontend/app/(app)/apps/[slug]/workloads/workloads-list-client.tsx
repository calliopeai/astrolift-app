"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  CalendarClockIcon,
  GlobeIcon,
  HardDriveIcon,
  LayersIcon,
  RocketIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import {
  GET_APP,
  LIST_WORKLOADS,
} from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  WorkloadKind,
} from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

const KIND_ICON: Record<
  WorkloadKind,
  React.ComponentType<{ className?: string }>
> = {
  deployment: RocketIcon,
  statefulset: HardDriveIcon,
  job: WorkflowIcon,
  cronjob: CalendarClockIcon,
};

const KIND_LABEL: Record<WorkloadKind, string> = {
  deployment: "Deployment",
  statefulset: "StatefulSet",
  job: "Job",
  cronjob: "CronJob",
};

export function WorkloadsListClient({ slug }: { slug: string }) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
    pollInterval: 60000,
  });

  const a = app.data?.astroliftApp;
  const list = workloads.data?.astroliftWorkloads ?? [];

  if (app.loading && !a) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title="App not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  // Surface stats up top so an operator immediately sees the shape of
  // the app's workload set before diving into rows.
  const totalReplicas = list.reduce((acc, w) => acc + (w.replicas || 0), 0);
  const publicCount = list.filter((w) => w.isPublic).length;
  const kindCounts = list.reduce<Partial<Record<WorkloadKind, number>>>(
    (acc, w) => {
      acc[w.kind] = (acc[w.kind] ?? 0) + 1;
      return acc;
    },
    {},
  );

  return (
    <PageShell
      title={`${a.name} · Workloads`}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {a.slug} · workloads parsed from the manifest at {a.manifestPath}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="workloads" />

      {/* ─── stats strip ───────────────────────────────────────────────── */}
      <div className="grid gap-3 sm:grid-cols-4 text-sm">
        <SummaryTile icon={LayersIcon} label="Workloads" value={list.length} />
        <SummaryTile
          icon={BoxIcon}
          label="Total replicas"
          value={totalReplicas}
        />
        <SummaryTile icon={GlobeIcon} label="Public" value={publicCount} />
        <SummaryTile
          icon={CalendarClockIcon}
          label="Scheduled"
          value={kindCounts.cronjob ?? 0}
        />
      </div>

      <Card>
        <CardContent className="p-0">
          {workloads.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title="No workloads declared"
                description="Workloads are parsed from this app's manifest. Add a workload section to astrolift.yaml and push to repopulate this view."
                actionHref={`/apps/${a.slug}/manifest`}
                actionLabel="Open manifest"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Replicas / HPA</TableHead>
                  <TableHead>Public</TableHead>
                  <TableHead>Resources</TableHead>
                  <TableHead>Schedule</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((w) => {
                  const Icon = KIND_ICON[w.kind] ?? BoxIcon;
                  return (
                    <TableRow
                      key={w.id}
                      className="hover:bg-accent/30 cursor-pointer"
                      onClick={() =>
                        (window.location.href = `/apps/${a.slug}/workloads/${w.slug}`)
                      }
                    >
                      <TableCell>
                        <Link
                          href={`/apps/${a.slug}/workloads/${w.slug}`}
                          className="flex items-center gap-2 hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <Icon className="text-muted-foreground size-4" />
                          <div>
                            <div className="font-medium">{w.name}</div>
                            <div className="text-muted-foreground font-mono text-xs">
                              {w.slug}
                            </div>
                          </div>
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="capitalize">
                          {KIND_LABEL[w.kind] ?? w.kind}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        <div>{w.replicas} replicas</div>
                        {w.hpaMinReplicas && w.hpaMaxReplicas ? (
                          <div className="text-muted-foreground">
                            HPA {w.hpaMinReplicas}–{w.hpaMaxReplicas} @{" "}
                            {w.hpaTargetCpuPct}% CPU
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        {w.isPublic ? (
                          <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
                            <GlobeIcon className="size-3" /> public
                          </Badge>
                        ) : (
                          <Badge variant="secondary">internal</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {w.cpuRequest || "—"} cpu · {w.memoryRequest || "—"}{" "}
                        mem
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {w.schedule ? (
                          <span className="inline-flex items-center gap-1">
                            <CalendarClockIcon className="size-3" />
                            {w.schedule}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}

function SummaryTile({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number;
}) {
  return (
    <div className="border-border bg-card flex items-center gap-3 rounded-md border p-3">
      <div className="bg-primary/10 text-primary rounded-md p-1.5">
        <Icon className="size-4" />
      </div>
      <div>
        <div className="text-muted-foreground text-xs uppercase tracking-wide">
          {label}
        </div>
        <div className="text-lg font-bold tabular-nums">{value}</div>
      </div>
    </div>
  );
}
