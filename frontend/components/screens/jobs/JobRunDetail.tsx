"use client";

import { HistoryIcon, InfoIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";

import { DetailTimestamp } from "@/components/detail/EntityDetailShell";
import { Identifier } from "@/components/Identifier";
import { RunStatusBadge } from "@/components/jobs/RunStatusBadge";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { RunPage } from "@/components/run/RunPage";
import { appsDetailCrumbs } from "@/components/screens/deployments/apps-area";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";

import {
  jobRunsHref,
  jobRunStatus,
  outputLines,
  runElapsed,
  runFailure,
  runSteps,
} from "./jobs-list";
import { RunMenu, RunMissing } from "./RunDetailParts";
import type { useJobRunDetail } from "./use-job-run-detail";

export type JobRunDetailProps = ReturnType<typeof useJobRunDetail> & { id: string };

/** Scheduled jobs ▾ › billing › nightly-report › run 4e1b0d8f (spec 44 §4.4). */
function crumbs(id: string, run: AstroliftScheduledJobRun | null) {
  const last = { label: `run ${id.slice(0, 8)}` };
  if (!run) return appsDetailCrumbs("jobs", last);
  return appsDetailCrumbs(
    "jobs",
    { label: run.registeredAppSlug, href: jobRunsHref(run.registeredAppSlug) },
    { label: run.workloadSlug, href: jobRunsHref(run.registeredAppSlug, run.workloadSlug) },
    last
  );
}

/**
 * One scheduled job run on the run archetype (spec 44 §5.5, #1106): waiting
 * for a pod, then the run, on the left; its captured output on the right; a
 * failure's exit code and last line first. Pure view; the data half is
 * useJobRunDetail.
 */
export function JobRunDetail({
  id,
  loading,
  run,
  error,
  onRetry,
  now,
  onDownload,
}: JobRunDetailProps) {
  if (!run && loading) {
    return (
      <RunPage
        crumbs={crumbs(id, null)}
        title={`run ${id.slice(0, 8)}`}
        steps={[]}
        stepsLoading
        log={{ lines: [], loading: true, title: "Output" }}
      />
    );
  }
  if (!run) {
    return (
      <RunMissing
        crumbs={crumbs(id, null)}
        title="Job run"
        icon={<HistoryIcon className="size-4" />}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <HistoryIcon className="size-5" />,
          title: "Job run not found",
          description: "No job run has this id, or you do not have permission to see it.",
          actionHref: "/jobs/runs",
          actionLabel: "Open job runs",
        }}
      />
    );
  }

  const status = jobRunStatus(run);
  const live = status === "running";
  const output = run.output ?? "";
  const logsHref = `/apps/${run.registeredAppSlug}/logs?${new URLSearchParams({
    workload: run.workloadSlug,
    run: run.k8sJobName || run.id,
  }).toString()}`;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <RunPage
        crumbs={crumbs(id, run)}
        title={`run ${id.slice(0, 8)}`}
        status={<RunStatusBadge status={status} exitCode={run.exitCode} />}
        durationMs={runElapsed(run, live, now)}
        context={<span className="font-mono">{run.environmentName || "—"}</span>}
        primaryAction={
          <Button size="sm" variant="outline" asChild>
            <Link href={logsHref}>
              <ScrollIcon className="size-4" />
              Open logs
            </Link>
          </Button>
        }
        menu={
          <RunMenu
            id={run.id}
            links={[
              { label: "Open app", href: `/apps/${run.registeredAppSlug}` },
              {
                label: "Open workload",
                href: `/apps/${run.registeredAppSlug}/workloads/${run.workloadSlug}`,
              },
            ]}
          />
        }
        steps={runSteps(
          run,
          status,
          run.k8sJobName || run.workloadSlug,
          now,
          run.exitCode != null ? `exit ${run.exitCode}` : undefined
        )}
        failure={runFailure(status, run.exitCode, output, run.logExcerpt ?? "")}
        log={{
          title: "Output",
          lines: outputLines(output, run.startedAt ?? run.createdAt),
          onDownload: output ? onDownload : undefined,
          emptyHint: live ? "No output yet." : "This run wrote no output.",
        }}
      />

      <PanelGrid>
        <Panel title="Details" icon={<InfoIcon className="size-4" />} span={6}>
          <DefinitionList
            items={[
              { term: "Run ID", description: <Identifier value={run.id} form="full" /> },
              {
                term: "App",
                description: (
                  <Link href={`/apps/${run.registeredAppSlug}`} className="hover:underline">
                    {run.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: "Environment",
                description: (
                  <span className="font-mono text-xs">{run.environmentName || "—"}</span>
                ),
              },
              {
                term: "Workload",
                description: (
                  <Link
                    href={`/apps/${run.registeredAppSlug}/workloads/${run.workloadSlug}`}
                    className="font-mono text-xs hover:underline"
                  >
                    {run.workloadSlug}
                  </Link>
                ),
              },
              {
                term: "K8s job",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {run.k8sJobName || "—"}
                  </span>
                ),
              },
              {
                term: "Exit code",
                description: <span className="font-mono">{run.exitCode ?? "—"}</span>,
              },
              { term: "Created", description: <DetailTimestamp iso={run.createdAt} /> },
              { term: "Started", description: <DetailTimestamp iso={run.startedAt} /> },
              { term: "Ended", description: <DetailTimestamp iso={run.endedAt} /> },
            ]}
          />
        </Panel>
      </PanelGrid>
    </div>
  );
}
