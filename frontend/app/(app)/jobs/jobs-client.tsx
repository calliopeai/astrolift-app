"use client";

import { useQuery } from "@apollo/client/react";
import { ClockIcon, TerminalIcon, CalendarClockIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  LIST_COMMAND_RUNS,
  LIST_SCHEDULED_JOB_RUNS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftCommandRun,
  AstroliftScheduledJobRun,
  ScheduledJobRunStatus,
} from "@/graphql/lifecycle/lifecycle.types";

interface JobResp {
  astroliftScheduledJobRuns: AstroliftScheduledJobRun[];
}

interface CmdResp {
  astroliftCommandRuns: AstroliftCommandRun[];
}

const jobStatusToDot: Record<
  ScheduledJobRunStatus,
  "ok" | "warn" | "error" | "muted" | "pending"
> = {
  running: "pending",
  succeeded: "ok",
  failed: "error",
  superseded: "muted",
};

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function JobsClient() {
  const [tab, setTab] = React.useState<"scheduled" | "commands">("scheduled");
  const { data: jobsData, loading: jobsLoading } = useQuery<JobResp>(
    LIST_SCHEDULED_JOB_RUNS,
    { variables: { limit: 100 }, pollInterval: 15000 },
  );
  const { data: cmdData, loading: cmdLoading } = useQuery<CmdResp>(
    LIST_COMMAND_RUNS,
    { variables: { limit: 100 }, pollInterval: 15000 },
  );
  const jobs = jobsData?.astroliftScheduledJobRuns ?? [];
  const cmds = cmdData?.astroliftCommandRuns ?? [];

  return (
    <PageShell
      title="Jobs"
      description="Scheduled job runs and ad-hoc command executions across all your apps."
    >
      <div className="flex items-center gap-2">
        <Button
          variant={tab === "scheduled" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("scheduled")}
          className="gap-2"
        >
          <CalendarClockIcon className="size-4" /> Scheduled
          <Badge variant="secondary" className="ml-1">
            {jobs.length}
          </Badge>
        </Button>
        <Button
          variant={tab === "commands" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("commands")}
          className="gap-2"
        >
          <TerminalIcon className="size-4" /> Commands
          <Badge variant="secondary" className="ml-1">
            {cmds.length}
          </Badge>
        </Button>
      </div>

      {tab === "scheduled" && (
          <Card>
            <CardContent className="p-0">
              {jobsLoading && jobs.length === 0 ? (
                <div className="space-y-2 p-6">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : jobs.length === 0 ? (
                <div className="p-6">
                  <EmptyState
                    icon={<CalendarClockIcon className="size-5" />}
                    title="No scheduled job runs yet"
                    description="Cronjob workloads land here as soon as the scheduler fires their first run."
                  />
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead></TableHead>
                      <TableHead>App / Env / Workload</TableHead>
                      <TableHead>K8s job</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Exit</TableHead>
                      <TableHead>Duration</TableHead>
                      <TableHead>Started</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {jobs.map((j) => (
                      <TableRow key={j.id}>
                        <TableCell className="w-8">
                          <StatusDot status={jobStatusToDot[j.status]} />
                        </TableCell>
                        <TableCell>
                          <div className="font-medium">
                            {j.registeredAppSlug}
                          </div>
                          <div className="text-muted-foreground text-xs">
                            env <span className="font-mono">{j.environmentName}</span>
                            {" · "}
                            workload <span className="font-mono">{j.workloadSlug}</span>
                          </div>
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {j.k8sJobName || "—"}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="capitalize">
                            {j.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {j.exitCode ?? "—"}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          <span className="inline-flex items-center gap-1">
                            <ClockIcon className="size-3" />
                            {formatDuration(j.durationSeconds)}
                          </span>
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                          {formatTime(j.startedAt)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
      )}

      {tab === "commands" && (
          <Card>
            <CardContent className="p-0">
              {cmdLoading && cmds.length === 0 ? (
                <div className="space-y-2 p-6">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : cmds.length === 0 ? (
                <div className="p-6">
                  <EmptyState
                    icon={<TerminalIcon className="size-5" />}
                    title="No command runs yet"
                    description="Ad-hoc commands invoked against an app workload land here. Run one from the CLI with `astro app run …`."
                  />
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>App / Workload</TableHead>
                      <TableHead>Command</TableHead>
                      <TableHead>Invoked by</TableHead>
                      <TableHead>Exit</TableHead>
                      <TableHead>Started</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {cmds.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>
                          <div className="font-medium">
                            {c.registeredAppSlug}
                          </div>
                          {c.workloadSlug && (
                            <div className="text-muted-foreground text-xs">
                              workload{" "}
                              <span className="font-mono">{c.workloadSlug}</span>
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {Array.isArray(c.command)
                            ? (c.command as string[]).join(" ")
                            : JSON.stringify(c.command)}
                        </TableCell>
                        <TableCell className="text-sm">
                          {c.invokedByUsername ?? "—"}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {c.exitCode ?? "—"}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                          {formatTime(c.startedAt)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
      )}
    </PageShell>
  );
}
