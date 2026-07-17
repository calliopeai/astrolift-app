"use client";

import { useQuery } from "@apollo/client/react";
import Link from "next/link";
import * as React from "react";

import { DetailStatusBadge, DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { RunOutputPanel } from "@/components/jobs/RunOutputPanel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LIST_SCHEDULED_JOB_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";

interface JobRunResp {
  astroliftScheduledJobRuns: AstroliftScheduledJobRun[];
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

/**
 * Scheduled job run detail (#1106). No singular query exists, so this reuses
 * the global LIST_SCHEDULED_JOB_RUNS window (same variables ⇒ cache hit from
 * the list). Carries the same inline `output` the /jobs run row expanded, now
 * on a linkable page alongside the full field grid.
 */
export function JobRunDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<JobRunResp>(LIST_SCHEDULED_JOB_RUNS, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
  });

  const run = React.useMemo(
    () => (data?.astroliftScheduledJobRuns ?? []).find((r) => r.id === id) ?? null,
    [data, id]
  );

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
              { term: "Run ID", description: <span className="font-mono text-xs break-all">{run.id}</span> },
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
                description: <span className="font-mono text-xs">{run.environmentName || "—"}</span>,
              },
              {
                term: "Workload",
                description: (
                  <Link
                    href={`/apps/${run.registeredAppSlug}/workloads/${run.workloadSlug}`}
                    className="text-[var(--brand-primary)] font-mono text-xs hover:underline"
                  >
                    {run.workloadSlug}
                  </Link>
                ),
              },
              {
                term: "K8s job",
                description: <span className="font-mono text-xs break-all">{run.k8sJobName || "—"}</span>,
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
