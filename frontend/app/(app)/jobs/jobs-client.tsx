"use client";

import { useQuery } from "@apollo/client/react";
import {
  CalendarClockIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  TerminalIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConcurrencyBadge } from "@/components/jobs/ConcurrencyBadge";
import { CronSchedulePreview } from "@/components/jobs/CronSchedulePreview";
import { RunOutputPanel } from "@/components/jobs/RunOutputPanel";
import { RunStatusBadge, commandRunStatus } from "@/components/jobs/RunStatusBadge";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import { LIST_COMMAND_RUNS, LIST_SCHEDULED_JOB_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftCommandRun,
  AstroliftScheduledJobRun,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import { cn } from "@/lib/utils";

interface JobResp {
  astroliftScheduledJobRuns: AstroliftScheduledJobRun[];
}

interface CmdResp {
  astroliftCommandRuns: AstroliftCommandRun[];
}

interface WorkloadCronCardData {
  id: string;
  slug: string;
  name: string;
  schedule: string;
  concurrencyPolicy: string;
}

interface WorkloadResp {
  astroliftWorkloads: Array<{
    id: string;
    slug: string;
    name: string;
    kind: string;
    schedule: string;
    concurrencyPolicy: string;
  }>;
}

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

function consoleHrefForJobRun(run: AstroliftScheduledJobRun): string {
  // Deep-link into the per-app logs surface so the operator can
  // grab the full tail when the inline 200-line view runs out.
  // The logs page understands ``?workload=`` + ``?run=`` so the
  // selection lands pre-filtered when the user clicks through.
  const params = new URLSearchParams({
    workload: run.workloadSlug,
    run: run.k8sJobName || run.id,
  });
  return `/apps/${run.registeredAppSlug}/logs?${params.toString()}`;
}

function consoleHrefForCommandRun(run: AstroliftCommandRun): string {
  const params = new URLSearchParams({ command: run.id });
  if (run.workloadSlug) params.set("workload", run.workloadSlug);
  return `/apps/${run.registeredAppSlug}/logs?${params.toString()}`;
}

export function JobsClient({ appSlug, tabs }: { appSlug?: string; tabs?: React.ReactNode } = {}) {
  const t = useTranslations("jobs");
  const [tab, setTab] = React.useState<"scheduled" | "commands">("scheduled");
  const variables = appSlug ? { appSlug, limit: 100 } : { limit: 100 };
  const { data: jobsData, loading: jobsLoading } = useQuery<JobResp>(LIST_SCHEDULED_JOB_RUNS, {
    variables,
    pollInterval: 15000,
  });
  const { data: cmdData, loading: cmdLoading } = useQuery<CmdResp>(LIST_COMMAND_RUNS, {
    variables,
    pollInterval: 15000,
  });

  // Per-app jobs page also surfaces the configured cronjob workloads
  // so an operator can see schedule + concurrency without scrolling
  // through fired runs first. Global /jobs skips this query —
  // there's no single app context to anchor it to.
  const { data: wlData } = useQuery<WorkloadResp>(LIST_WORKLOADS, {
    variables: { appSlug },
    skip: !appSlug,
    fetchPolicy: "cache-and-network",
  });

  const jobs = jobsData?.astroliftScheduledJobRuns ?? [];
  const cmds = cmdData?.astroliftCommandRuns ?? [];
  const cronWorkloads: WorkloadCronCardData[] = React.useMemo(() => {
    const rows = wlData?.astroliftWorkloads ?? [];
    return rows
      .filter((w) => w.kind === "cronjob" && w.schedule)
      .map((w) => ({
        id: w.id,
        slug: w.slug,
        name: w.name,
        schedule: w.schedule,
        concurrencyPolicy: w.concurrencyPolicy,
      }));
  }, [wlData?.astroliftWorkloads]);

  return (
    <PageShell
      title={t("title")}
      description={
        appSlug ? (
          <span className="text-muted-foreground font-mono text-xs">
            {t("descriptionForApp", { app: appSlug })}
          </span>
        ) : (
          t("descriptionGlobal")
        )
      }
    >
      {tabs}

      {appSlug && cronWorkloads.length > 0 ? (
        <CronWorkloadsCard appSlug={appSlug} workloads={cronWorkloads} />
      ) : null}

      <div className="flex items-center gap-2">
        <Button
          variant={tab === "scheduled" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("scheduled")}
          className="gap-2"
        >
          <CalendarClockIcon className="size-4" /> {t("tabs.scheduled")}
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
          <TerminalIcon className="size-4" /> {t("tabs.commands")}
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
                  title={t("scheduled.emptyTitle")}
                  description={t("scheduled.emptyDescription")}
                />
              </div>
            ) : (
              <ScheduledJobRunsTable jobs={jobs} />
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
                  title={t("commands.emptyTitle")}
                  description={t("commands.emptyDescription")}
                />
              </div>
            ) : (
              <CommandRunsTable cmds={cmds} />
            )}
          </CardContent>
        </Card>
      )}
    </PageShell>
  );
}

function CronWorkloadsCard({
  appSlug,
  workloads,
}: {
  appSlug: string;
  workloads: WorkloadCronCardData[];
}) {
  const t = useTranslations("jobs.workloadCard");
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-6">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{t("title")}</h2>
          <span className="text-muted-foreground text-xs">
            {t("countLabel", { count: workloads.length })}
          </span>
        </div>
        <ul className="divide-border divide-y">
          {workloads.map((w) => (
            <li
              key={w.id}
              className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <code className="font-mono text-sm font-medium">{w.slug}</code>
                  <ConcurrencyBadge policy={w.concurrencyPolicy} />
                </div>
                <CronSchedulePreview schedule={w.schedule} />
              </div>
              <Button asChild size="sm" variant="outline">
                <a href={`/apps/${appSlug}/logs?workload=${w.slug}`}>{t("openLogs")}</a>
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function ScheduledJobRunsTable({ jobs }: { jobs: AstroliftScheduledJobRun[] }) {
  const t = useTranslations("jobs.scheduled");
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>{t("columns.app")}</TableHead>
          <TableHead>{t("columns.k8sJob")}</TableHead>
          <TableHead>{t("columns.status")}</TableHead>
          <TableHead>{t("columns.duration")}</TableHead>
          <TableHead>{t("columns.started")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((j) => {
          const isOpen = expanded.has(j.id);
          return (
            <React.Fragment key={j.id}>
              <TableRow
                className={cn("cursor-pointer", isOpen && "bg-muted/30")}
                onClick={() => toggle(j.id)}
              >
                <TableCell className="w-8">
                  {isOpen ? (
                    <ChevronDownIcon className="size-4" />
                  ) : (
                    <ChevronRightIcon className="size-4" />
                  )}
                </TableCell>
                <TableCell>
                  <div className="font-medium">{j.registeredAppSlug}</div>
                  <div className="text-muted-foreground text-xs">
                    {t("envLabel")} <span className="font-mono">{j.environmentName}</span>
                    {" · "}
                    {t("workloadLabel")} <span className="font-mono">{j.workloadSlug}</span>
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs">{j.k8sJobName || "—"}</TableCell>
                <TableCell>
                  <RunStatusBadge status={j.status} exitCode={j.exitCode} />
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
              {isOpen ? (
                <TableRow className="bg-muted/30 hover:bg-muted/30">
                  <TableCell />
                  <TableCell colSpan={5} className="py-3">
                    <RunOutputPanel
                      output={j.output ?? ""}
                      consoleHref={consoleHrefForJobRun(j)}
                      caption={j.k8sJobName || j.workloadSlug}
                    />
                  </TableCell>
                </TableRow>
              ) : null}
            </React.Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}

function CommandRunsTable({ cmds }: { cmds: AstroliftCommandRun[] }) {
  const t = useTranslations("jobs.commands");
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>{t("columns.app")}</TableHead>
          <TableHead>{t("columns.command")}</TableHead>
          <TableHead>{t("columns.status")}</TableHead>
          <TableHead>{t("columns.invokedBy")}</TableHead>
          <TableHead>{t("columns.started")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {cmds.map((c) => {
          const isOpen = expanded.has(c.id);
          const status = commandRunStatus({
            endedAt: c.endedAt,
            exitCode: c.exitCode,
          });
          return (
            <React.Fragment key={c.id}>
              <TableRow
                className={cn("cursor-pointer", isOpen && "bg-muted/30")}
                onClick={() => toggle(c.id)}
              >
                <TableCell className="w-8">
                  {isOpen ? (
                    <ChevronDownIcon className="size-4" />
                  ) : (
                    <ChevronRightIcon className="size-4" />
                  )}
                </TableCell>
                <TableCell>
                  <div className="font-medium">{c.registeredAppSlug}</div>
                  {c.workloadSlug ? (
                    <div className="text-muted-foreground text-xs">
                      {t("workloadLabel")} <span className="font-mono">{c.workloadSlug}</span>
                    </div>
                  ) : null}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {Array.isArray(c.command)
                    ? (c.command as string[]).join(" ")
                    : JSON.stringify(c.command)}
                </TableCell>
                <TableCell>
                  <RunStatusBadge status={status} exitCode={c.exitCode} />
                </TableCell>
                <TableCell className="text-sm">{c.invokedByUsername ?? "—"}</TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {formatTime(c.startedAt)}
                </TableCell>
              </TableRow>
              {isOpen ? (
                <TableRow className="bg-muted/30 hover:bg-muted/30">
                  <TableCell />
                  <TableCell colSpan={5} className="py-3">
                    <RunOutputPanel
                      output={c.output ?? ""}
                      consoleHref={consoleHrefForCommandRun(c)}
                      caption={
                        Array.isArray(c.command) ? (c.command as string[]).join(" ") : undefined
                      }
                    />
                  </TableCell>
                </TableRow>
              ) : null}
            </React.Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}
