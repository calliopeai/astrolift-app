"use client";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  BoxIcon,
  GitBranchIcon,
  GlobeIcon,
  HardDriveIcon,
} from "lucide-react";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_SCHEDULED_JOB_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";
import {
  GET_WORKLOAD,
  LIST_CONTAINERS,
} from "@/graphql/registry/registry.queries";
import type {
  AstroliftContainer,
  AstroliftWorkload,
} from "@/graphql/registry/registry.types";

interface WorkloadResp {
  astroliftWorkload: AstroliftWorkload | null;
}

interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
}

interface JobRunsResp {
  astroliftScheduledJobRuns: AstroliftScheduledJobRun[];
}

const HEALTHCHECK_LABEL: Record<string, string> = {
  none: "no probe",
  http: "HTTP probe",
  tcp: "TCP probe",
  exec: "exec probe",
};

export function WorkloadDetailClient({
  appSlug,
  workloadSlug,
}: {
  appSlug: string;
  workloadSlug: string;
}) {
  const { data: wlData, loading: wlLoading } = useQuery<WorkloadResp>(
    GET_WORKLOAD,
    { variables: { appSlug, slug: workloadSlug } },
  );
  const { data: cData, loading: cLoading } = useQuery<ContainersResp>(
    LIST_CONTAINERS,
    { variables: { workloadSlug } },
  );
  const w = wlData?.astroliftWorkload ?? null;

  const isCronjob = w?.kind === "cronjob";
  const { data: rData, loading: rLoading } = useQuery<JobRunsResp>(
    LIST_SCHEDULED_JOB_RUNS,
    {
      variables: { appSlug, limit: 10 },
      skip: !isCronjob,
      pollInterval: isCronjob ? 15000 : 0,
    },
  );

  const containers = cData?.astroliftContainers ?? [];
  const runs = (rData?.astroliftScheduledJobRuns ?? []).filter(
    (r) => r.workloadSlug === workloadSlug,
  );

  if (wlLoading && !w) {
    return (
      <PageShell title="Workload" description="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!w) {
    return (
      <PageShell
        title="Workload not found"
        description="The workload doesn't exist or you don't have permission to view it."
      >
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            Return to the{" "}
            <a href={`/apps/${appSlug}`} className="underline">
              app overview
            </a>
            .
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${appSlug} · ${w.slug}`}
      description={`${w.kind} workload from the manifest. ${w.replicas} replica${w.replicas === 1 ? "" : "s"}.`}
      actions={
        <Button variant="outline" asChild>
          <a href={`/apps/${appSlug}/manifest`}>
            <GitBranchIcon className="size-4" />
            Manifest preview
          </a>
        </Button>
      }
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            <BoxIcon className="size-4" />
            <span className="font-mono">{w.slug}</span>
            <Badge variant="secondary" className="capitalize">
              {w.kind}
            </Badge>
            {w.isPublic && (
              <Badge variant="outline" className="gap-1">
                <GlobeIcon className="size-3" /> public
              </Badge>
            )}
            {w.schedule && (
              <Badge variant="outline" className="font-mono">
                cron {w.schedule}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:grid-cols-3">
          <Field label="Replicas" mono value={w.replicas} />
          <Field
            label="HPA"
            mono
            value={
              w.hpaMinReplicas && w.hpaMaxReplicas
                ? `${w.hpaMinReplicas} - ${w.hpaMaxReplicas} @ ${w.hpaTargetCpuPct}% cpu`
                : "off"
            }
          />
          <Field
            label="CPU req/limit"
            mono
            value={`${w.cpuRequest || "—"} / ${w.cpuLimit || "—"}`}
          />
          <Field
            label="Memory req/limit"
            mono
            value={`${w.memoryRequest || "—"} / ${w.memoryLimit || "—"}`}
          />
          <Field
            label="Storage"
            mono
            value={
              w.storageClass || w.storageSize
                ? `${w.storageClass || "default"} / ${w.storageSize || "—"}`
                : "—"
            }
          />
        </CardContent>
      </Card>

      {isCronjob && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Recent runs
              <Badge variant="outline" className="ml-2">
                {runs.length}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {rLoading && runs.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : runs.length === 0 ? (
              <div className="text-muted-foreground p-6 text-sm">
                No scheduled runs recorded yet.
              </div>
            ) : (
              <ul className="divide-y">
                {runs.map((r) => (
                  <li
                    key={r.id}
                    className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary" className="capitalize">
                          {r.status}
                        </Badge>
                        <span className="font-mono text-xs">
                          {r.k8sJobName || r.id.slice(0, 12)}
                        </span>
                      </div>
                      <div className="text-muted-foreground mt-0.5 text-xs">
                        env <span className="font-mono">{r.environmentName}</span>
                        {r.startedAt && (
                          <>
                            {" · "}started{" "}
                            {new Date(r.startedAt).toLocaleString()}
                          </>
                        )}
                        {r.durationSeconds != null && (
                          <>
                            {" · "}
                            {r.durationSeconds < 60
                              ? `${r.durationSeconds}s`
                              : `${Math.floor(r.durationSeconds / 60)}m ${r.durationSeconds % 60}s`}
                          </>
                        )}
                      </div>
                    </div>
                    <code className="text-muted-foreground font-mono text-xs">
                      exit {r.exitCode ?? "—"}
                    </code>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Containers
            <Badge variant="outline" className="ml-2">
              {containers.length}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {cLoading && containers.length === 0 ? (
            <Skeleton className="h-24 w-full" />
          ) : containers.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              This workload has no containers declared in the manifest.
            </p>
          ) : (
            containers.map((c) => (
              <div
                key={c.id}
                className="border-muted rounded-md border p-3 text-sm"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="font-mono">{c.name}</span>
                  {c.isPrimary && (
                    <Badge variant="default" className="text-xs">
                      primary
                    </Badge>
                  )}
                  {c.port > 0 && (
                    <Badge variant="outline" className="font-mono text-xs">
                      :{c.port}
                    </Badge>
                  )}
                  <Badge variant="secondary" className="ml-auto gap-1">
                    <ActivityIcon className="size-3" />
                    {HEALTHCHECK_LABEL[c.healthcheckKind] ?? c.healthcheckKind}
                  </Badge>
                </div>
                <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                  {c.imageRef && (
                    <Field label="Image ref" mono value={c.imageRef} />
                  )}
                  {c.dockerfilePath && c.dockerfilePath !== "Dockerfile" && (
                    <Field label="Dockerfile" mono value={c.dockerfilePath} />
                  )}
                  {c.buildContext && c.buildContext !== "." && (
                    <Field label="Build context" mono value={c.buildContext} />
                  )}
                  {c.command.length > 0 && (
                    <Field
                      label="Command"
                      mono
                      value={c.command.join(" ")}
                    />
                  )}
                  {c.args.length > 0 && (
                    <Field label="Args" mono value={c.args.join(" ")} />
                  )}
                  {c.healthcheckKind !== "none" && c.healthcheckValue && (
                    <Field
                      label="Probe target"
                      mono
                      value={c.healthcheckValue}
                    />
                  )}
                </dl>
                {c.env && Object.keys(c.env).length > 0 && (
                  <div className="mt-3">
                    <div className="text-muted-foreground mb-1 text-xs uppercase tracking-wide">
                      Env ({Object.keys(c.env).length})
                    </div>
                    <pre className="bg-muted overflow-auto rounded p-2 text-xs">
                      {Object.entries(c.env)
                        .map(([k, v]) => `${k}=${v}`)
                        .join("\n")}
                    </pre>
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </dt>
      <dd className={mono ? "font-mono text-sm" : "text-sm"}>{value}</dd>
    </div>
  );
}
