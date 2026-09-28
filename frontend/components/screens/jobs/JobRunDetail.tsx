"use client";

import Link from "next/link";

import {
  DetailStatusBadge,
  DetailTimestamp,
  EntityDetailShell,
} from "@/components/detail/EntityDetailShell";
import { RunOutputPanel } from "@/components/jobs/RunOutputPanel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { formatDuration } from "./jobs-format";
import type { useJobRunDetail } from "./use-job-run-detail";

export type JobRunDetailProps = ReturnType<typeof useJobRunDetail> & { id: string };

/**
 * Scheduled job run detail (#1106, #1118). Carries the same inline `output`
 * the /jobs run row expanded, now on a linkable page alongside the full
 * field grid. Data comes from useJobRunDetail.
 */
export function JobRunDetail({ id, loading, run }: JobRunDetailProps) {
  const consoleHref = run
    ? `/apps/${run.registeredAppSlug}/logs?${new URLSearchParams({
        workload: run.workloadSlug,
        run: run.k8sJobName || run.id,
      }).toString()}`
    : "#";

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!run}
      breadcrumb={{ label: "Jobs", href: "/jobs?tab=runs" }}
      heading={`Run ${id.slice(0, 8)}`}
      status={run?.status}
      createdAt={run?.createdAt}
      notFoundLabel="job run"
      overview={
        run
          ? [
              { term: "Status", description: <DetailStatusBadge status={run.status} /> },
              {
                term: "Run ID",
                description: <span className="font-mono text-xs break-all">{run.id}</span>,
              },
              {
                term: "App",
                description: (
                  <Link
                    href={`/apps/${run.registeredAppSlug}`}
                    className="text-[var(--brand-primary)] hover:underline"
                  >
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
                    className="font-mono text-xs text-[var(--brand-primary)] hover:underline"
                  >
                    {run.workloadSlug}
                  </Link>
                ),
              },
              {
                term: "K8s job",
                description: (
                  <span className="font-mono text-xs break-all">{run.k8sJobName || "—"}</span>
                ),
              },
              {
                term: "Exit code",
                description:
                  run.exitCode == null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <span className="font-mono">{run.exitCode}</span>
                  ),
              },
              { term: "Duration", description: formatDuration(run.durationSeconds) },
              { term: "Created", description: <DetailTimestamp iso={run.createdAt} /> },
              { term: "Started", description: <DetailTimestamp iso={run.startedAt} /> },
              { term: "Ended", description: <DetailTimestamp iso={run.endedAt} /> },
            ]
          : []
      }
    >
      {run ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Output</CardTitle>
          </CardHeader>
          <CardContent>
            <RunOutputPanel
              output={run.output ?? ""}
              consoleHref={consoleHref}
              caption={run.k8sJobName || run.workloadSlug}
            />
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
