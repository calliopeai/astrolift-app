"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CalendarClockIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  HistoryIcon,
  Loader2Icon,
  PlayIcon,
  TerminalIcon,
  XCircleIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConcurrencyBadge } from "@/components/jobs/ConcurrencyBadge";
import { CronSchedulePreview } from "@/components/jobs/CronSchedulePreview";
import { RunOutputPanel } from "@/components/jobs/RunOutputPanel";
import { RunStatusBadge, commandRunStatus } from "@/components/jobs/RunStatusBadge";
import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { MiniBar } from "@/components/viz";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RUN_JOB_ONCE } from "@/graphql/lifecycle/lifecycle.mutations";
import {
  LIST_COMMAND_RUNS,
  LIST_ENVIRONMENTS,
  LIST_SCHEDULED_JOB_RUNS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftCommandRun,
  AstroliftScheduledJobRun,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import { useListControls, type ListControlsResult, type SortState } from "@/hooks/use-list-controls";
import { useFormatters } from "@/lib/i18n/formatters";
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

// Kept out of the component body so the impure Date.now() isn't a render-phase
// call (react-hooks/purity). Display-only; recomputed each poll.
function summarizeJobRuns(runs: AstroliftScheduledJobRun[], window: number) {
  const perDay = new Array<number>(window).fill(0);
  const now = Date.now();
  const counts = { succeeded: 0, failed: 0, running: 0 };
  for (const r of runs) {
    const t = Date.parse(r.startedAt ?? r.createdAt);
    if (!Number.isNaN(t)) {
      const ago = Math.floor((now - t) / 86_400_000);
      if (ago >= 0 && ago < window) perDay[window - 1 - ago] += 1;
    }
    if (r.status === "succeeded") counts.succeeded += 1;
    else if (r.status === "failed") counts.failed += 1;
    else if (r.status === "running") counts.running += 1;
  }
  return { perDay, counts };
}

// At-a-glance overview above the runs table: 14-day run volume + terminal
// outcome counts, from the already-fetched run list (no extra query).
function JobRunsSummary({ runs }: { runs: AstroliftScheduledJobRun[] }) {
  const WINDOW = 14;
  const { perDay, counts } = summarizeJobRuns(runs, WINDOW);

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 border-b px-4 py-3">
      <div className="flex flex-wrap items-center gap-3 text-xs tabular-nums">
        <span className="text-success-fg font-medium">{counts.succeeded} succeeded</span>
        <span className="text-danger-fg font-medium">{counts.failed} failed</span>
        <span className="text-info-fg font-medium">{counts.running} running</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-2xs uppercase tracking-wide">
          {WINDOW}-day volume
        </span>
        <MiniBar data={perDay} width={180} height={32} className="text-chart-1" />
      </div>
    </div>
  );
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

function jobRunSortFn(a: AstroliftScheduledJobRun, b: AstroliftScheduledJobRun, sort: SortState): number {
  const dir = sort.dir === "asc" ? 1 : -1;
  switch (sort.key) {
    case "app":
      return dir * a.registeredAppSlug.localeCompare(b.registeredAppSlug);
    case "status":
      return dir * a.status.localeCompare(b.status);
    case "duration":
      return dir * ((a.durationSeconds ?? 0) - (b.durationSeconds ?? 0));
    case "startedAt":
      return dir * ((a.startedAt ?? "").localeCompare(b.startedAt ?? ""));
    default:
      return 0;
  }
}

function cmdRunSortFn(a: AstroliftCommandRun, b: AstroliftCommandRun, sort: SortState): number {
  const dir = sort.dir === "asc" ? 1 : -1;
  switch (sort.key) {
    case "app":
      return dir * a.registeredAppSlug.localeCompare(b.registeredAppSlug);
    case "status": {
      const aStatus = commandRunStatus({ endedAt: a.endedAt, exitCode: a.exitCode });
      const bStatus = commandRunStatus({ endedAt: b.endedAt, exitCode: b.exitCode });
      return dir * aStatus.localeCompare(bStatus);
    }
    case "invokedBy":
      return dir * ((a.invokedByUsername ?? "").localeCompare(b.invokedByUsername ?? ""));
    case "startedAt":
      return dir * ((a.startedAt ?? "").localeCompare(b.startedAt ?? ""));
    default:
      return 0;
  }
}

function cronWorkloadSortFn(a: WorkloadCronCardData, b: WorkloadCronCardData, sort: SortState): number {
  const dir = sort.dir === "asc" ? 1 : -1;
  switch (sort.key) {
    case "slug":
      return dir * a.slug.localeCompare(b.slug);
    case "schedule":
      return dir * a.schedule.localeCompare(b.schedule);
    default:
      return 0;
  }
}

export function JobsClient({ appSlug, tabs }: { appSlug?: string; tabs?: React.ReactNode } = {}) {
  const t = useTranslations("jobs");
  const [tab, setTab] = React.useState<"schedules" | "runs" | "failures" | "commands">("schedules");
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
  // Failures tab is a pure client-side slice of the same run history —
  // ``status`` is the stored discriminator on ScheduledJobRun and
  // "failed" is the only terminal-error value (see RunStatusBadge).
  // Memoize off the query result, not the ``jobs`` fallback, so the
  // ``[] ?? `` default doesn't churn the dep on every render.
  const failedJobs = React.useMemo(
    () => (jobsData?.astroliftScheduledJobRuns ?? []).filter((j) => j.status === "failed"),
    [jobsData?.astroliftScheduledJobRuns]
  );
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

  const runsCtrl = useListControls({
    data: jobs,
    searchFn: (j) => [j.registeredAppSlug, j.workloadSlug, j.k8sJobName, j.status, j.environmentName].join(" "),
    initialPageSize: 25,
    initialSort: { key: "startedAt", dir: "desc" },
    sortFn: jobRunSortFn,
  });

  const failuresCtrl = useListControls({
    data: failedJobs,
    searchFn: (j) => [j.registeredAppSlug, j.workloadSlug, j.k8sJobName, j.environmentName].join(" "),
    initialPageSize: 25,
    initialSort: { key: "startedAt", dir: "desc" },
    sortFn: jobRunSortFn,
  });

  const cmdsCtrl = useListControls({
    data: cmds,
    searchFn: (c) => [
      c.registeredAppSlug,
      c.workloadSlug ?? "",
      c.invokedByUsername ?? "",
      Array.isArray(c.command) ? (c.command as string[]).join(" ") : JSON.stringify(c.command),
    ].join(" "),
    initialPageSize: 25,
    initialSort: { key: "startedAt", dir: "desc" },
    sortFn: cmdRunSortFn,
  });

  const schedulesCtrl = useListControls({
    data: cronWorkloads,
    searchFn: (w) => [w.slug, w.name, w.schedule].join(" "),
    initialPageSize: 25,
    sortFn: cronWorkloadSortFn,
  });

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

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={tab === "schedules" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("schedules")}
          className="gap-2"
        >
          <CalendarClockIcon className="size-4" /> {t("tabs.schedules")}
          {appSlug ? (
            <Badge variant="secondary" className="ml-1">
              {cronWorkloads.length}
            </Badge>
          ) : null}
        </Button>
        <Button
          variant={tab === "runs" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("runs")}
          className="gap-2"
        >
          <HistoryIcon className="size-4" /> {t("tabs.runs")}
          <Badge variant="secondary" className="ml-1">
            {jobs.length}
          </Badge>
        </Button>
        <Button
          variant={tab === "failures" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("failures")}
          className="gap-2"
        >
          <XCircleIcon className="size-4" /> {t("tabs.failures")}
          <Badge variant={failedJobs.length > 0 ? "destructive" : "secondary"} className="ml-1">
            {failedJobs.length}
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

      {tab === "schedules" &&
        (!appSlug ? (
          <Card>
            <CardContent className="p-6">
              <EmptyState
                icon={<CalendarClockIcon className="size-5" />}
                title={t("schedules.emptyTitle")}
                description={t("schedules.emptyForFleet")}
              />
            </CardContent>
          </Card>
        ) : cronWorkloads.length > 0 ? (
          <CronWorkloadsCard appSlug={appSlug} workloads={schedulesCtrl.rows} ctrl={schedulesCtrl} />
        ) : (
          <Card>
            <CardContent className="p-6">
              <EmptyState
                icon={<CalendarClockIcon className="size-5" />}
                title={t("schedules.emptyTitle")}
                description={t("schedules.emptyDescription")}
                learnMoreHref="https://github.com/calliopeai/astrolift-docs/blob/main/reference/manifest.md#jobs"
                learnMoreLabel={t("schedules.emptyLearnMore")}
                secondary={<ScheduledJobsExample caption={t("scheduled.emptyExampleCaption")} />}
              />
            </CardContent>
          </Card>
        ))}

      {tab === "runs" && (
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
                  icon={<HistoryIcon className="size-5" />}
                  title={t("scheduled.emptyTitle")}
                  description={t("scheduled.emptyDescription")}
                  learnMoreHref="https://github.com/calliopeai/astrolift-docs/blob/main/reference/manifest.md#jobs"
                  learnMoreLabel={t("scheduled.emptyLearnMore")}
                  secondary={<ScheduledJobsExample caption={t("scheduled.emptyExampleCaption")} />}
                />
              </div>
            ) : (
              <>
                <JobRunsSummary runs={jobs} />
                <div className="px-4 pt-4 pb-2">
                  <ListControls controls={runsCtrl} searchPlaceholder="Filter runs…" />
                </div>
                <ScheduledJobRunsTable jobs={runsCtrl.rows} ctrl={runsCtrl} />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "failures" && (
        <Card>
          <CardContent className="p-0">
            {jobsLoading && jobs.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : failedJobs.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<XCircleIcon className="size-5" />}
                  title={t("failures.emptyTitle")}
                  description={t("failures.emptyDescription")}
                />
              </div>
            ) : (
              <>
                <div className="px-4 pt-4 pb-2">
                  <ListControls controls={failuresCtrl} searchPlaceholder="Filter failures…" />
                </div>
                <ScheduledJobRunsTable jobs={failuresCtrl.rows} ctrl={failuresCtrl} />
              </>
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
              <>
                <div className="px-4 pt-4 pb-2">
                  <ListControls controls={cmdsCtrl} searchPlaceholder="Filter commands…" />
                </div>
                <CommandRunsTable cmds={cmdsCtrl.rows} ctrl={cmdsCtrl} />
              </>
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
  ctrl,
}: {
  appSlug: string;
  workloads: WorkloadCronCardData[];
  ctrl: ListControlsResult<WorkloadCronCardData>;
}) {
  const t = useTranslations("jobs.workloadCard");
  // #670 — environments + run-now mutation. Defaults to the first env
  // (typically 'production'); operator can switch via the per-row
  // select if they want to fire the job into a preview env instead.
  const envs = useQuery<{ astroliftEnvironments: AstroliftAppEnvironment[] }>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envList = envs.data?.astroliftEnvironments ?? [];
  const defaultEnv = envList[0]?.name ?? "";
  const [perRowEnv, setPerRowEnv] = React.useState<Record<string, string>>({});
  const [pendingSlug, setPendingSlug] = React.useState<string | null>(null);

  const [runOnce] = useMutation<{
    runAstroliftJobOnce: {
      ok: boolean;
      errors: { code: string; message: string }[];
      data: { runName: string; namespace: string; logsUrl: string } | null;
    };
  }>(RUN_JOB_ONCE, {
    refetchQueries: [{ query: LIST_SCHEDULED_JOB_RUNS, variables: { appSlug, limit: 100 } }],
  });

  async function handleRun(jobSlug: string) {
    const envName = perRowEnv[jobSlug] ?? defaultEnv;
    if (!envName) {
      toast.error("Pick an environment first.");
      return;
    }
    setPendingSlug(jobSlug);
    try {
      const { data } = await runOnce({
        variables: { input: { appSlug, environmentName: envName, jobSlug } },
      });
      const res = data?.runAstroliftJobOnce;
      if (res?.ok) {
        toast.success(`${jobSlug} dispatched as ${res.data?.runName ?? "manual run"} (${envName})`);
      } else {
        toast.error(res?.errors?.[0]?.message ?? "Run failed");
      }
    } finally {
      setPendingSlug(null);
    }
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-6">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{t("title")}</h2>
          <span className="text-muted-foreground text-xs">
            {t("countLabel", { count: workloads.length })}
          </span>
        </div>
        <ListControls controls={ctrl} searchPlaceholder="Filter schedules…" />
        <ul className="divide-border divide-y">
          {workloads.map((w) => {
            const envName = perRowEnv[w.slug] ?? defaultEnv;
            const isPending = pendingSlug === w.slug;
            return (
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
                <div className="flex items-center gap-2">
                  {envList.length > 1 && (
                    <Select
                      value={envName}
                      onValueChange={(v) => setPerRowEnv((prev) => ({ ...prev, [w.slug]: v }))}
                    >
                      <SelectTrigger className="h-8 w-32 text-xs">
                        <SelectValue placeholder="env" />
                      </SelectTrigger>
                      <SelectContent>
                        {envList.map((e) => (
                          <SelectItem key={e.id} value={e.name}>
                            {e.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleRun(w.slug)}
                    disabled={isPending || !envName}
                  >
                    {isPending ? (
                      <Loader2Icon className="size-4 animate-spin" />
                    ) : (
                      <PlayIcon className="size-4" />
                    )}
                    Run now
                  </Button>
                  <Button asChild size="sm" variant="ghost">
                    <a href={`/apps/${appSlug}/logs?workload=${w.slug}`}>{t("openLogs")}</a>
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function ScheduledJobsExample({ caption }: { caption: string }) {
  // Concrete worked example of a `[[jobs]]` block — picked to mirror the
  // shape an operator will paste into their astrolift.toml verbatim:
  // slug, image, the cron `schedule`, and the `command` to run.
  // Kept to 5 lines so the empty state stays visually compact.
  const example = [
    "[[jobs]]",
    'slug = "nightly-report"',
    'image = "ghcr.io/acme/reports:latest"',
    'schedule = "0 2 * * *"   # 02:00 UTC daily',
    'command = ["python", "-m", "reports.nightly"]',
  ].join("\n");
  return (
    <div className="mt-2 w-full max-w-xl text-left">
      <p className="text-muted-foreground mb-2 text-xs">{caption}</p>
      <pre className="bg-muted text-muted-foreground overflow-x-auto rounded-md border p-3 font-mono text-2xs leading-relaxed">
        {example}
      </pre>
    </div>
  );
}

function ScheduledJobRunsTable({
  jobs,
  ctrl,
}: {
  jobs: AstroliftScheduledJobRun[];
  ctrl: ListControlsResult<AstroliftScheduledJobRun>;
}) {
  const t = useTranslations("jobs.scheduled");
  const fmt = useFormatters();
  const formatTime = (iso: string | null | undefined) => (iso ? fmt.formatDateTime(iso) : "—");
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
          <TableHead>
            <SortableHeader sortKey="app" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.app")}
            </SortableHeader>
          </TableHead>
          <TableHead>{t("columns.k8sJob")}</TableHead>
          <TableHead>
            <SortableHeader sortKey="status" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.status")}
            </SortableHeader>
          </TableHead>
          <TableHead>
            <SortableHeader sortKey="duration" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.duration")}
            </SortableHeader>
          </TableHead>
          <TableHead>
            <SortableHeader sortKey="startedAt" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.started")}
            </SortableHeader>
          </TableHead>
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

function CommandRunsTable({
  cmds,
  ctrl,
}: {
  cmds: AstroliftCommandRun[];
  ctrl: ListControlsResult<AstroliftCommandRun>;
}) {
  const t = useTranslations("jobs.commands");
  const fmt = useFormatters();
  const formatTime = (iso: string | null | undefined) => (iso ? fmt.formatDateTime(iso) : "—");
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
          <TableHead>
            <SortableHeader sortKey="app" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.app")}
            </SortableHeader>
          </TableHead>
          <TableHead>{t("columns.command")}</TableHead>
          <TableHead>
            <SortableHeader sortKey="status" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.status")}
            </SortableHeader>
          </TableHead>
          <TableHead>
            <SortableHeader sortKey="invokedBy" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.invokedBy")}
            </SortableHeader>
          </TableHead>
          <TableHead>
            <SortableHeader sortKey="startedAt" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
              {t("columns.started")}
            </SortableHeader>
          </TableHead>
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
