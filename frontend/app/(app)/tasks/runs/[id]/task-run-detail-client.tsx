"use client";

import { useQuery } from "@apollo/client/react";
import { ScrollIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DetailStatusBadge, DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";

interface TaskRunResp {
  astroliftTaskRuns: AstroliftTaskRun[];
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

/**
 * Task run detail (#1106). No singular `astroliftTaskRun(id)` query exists, so
 * this reuses the same `LIST_TASK_RUNS` query the /tasks list uses (identical
 * variables ⇒ an Apollo cache hit when navigated from the list; a background
 * refetch otherwise). A run older than the 100-row window won't resolve on a
 * cold deep-link — it renders the shell's not-found state. `errorPolicy:
 * "ignore"` mirrors the list so a not-yet-wired backend field degrades to
 * not-found rather than throwing.
 */
export function TaskRunDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<TaskRunResp>(LIST_TASK_RUNS, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
    errorPolicy: "ignore",
  });

  const run = React.useMemo(
    () => (data?.astroliftTaskRuns ?? []).find((r) => r.id === id) ?? null,
    [data, id]
  );

  const command = run
    ? Array.isArray(run.command)
      ? run.command.join(" ")
      : String(run.command ?? "")
    : "";

  // Deep-link into the per-app logs console for the full tail — task runs
  // carry no inline `output` field, so the console is the only log surface.
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
      breadcrumb={{ label: "Tasks", href: "/tasks?tab=history" }}
      heading={`Run ${id.slice(0, 8)}`}
      status={run?.status}
      createdAt={run?.createdAt}
      notFoundLabel="task run"
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
                term: "Command",
                description: <span className="font-mono text-xs break-all">{command || "—"}</span>,
              },
              {
                term: "Trigger",
                description: (
                  <span>
                    {run.triggerKind}
                    {run.triggeredByUsername ? ` · ${run.triggeredByUsername}` : ""}
                  </span>
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
              {
                term: "K8s job",
                description: <span className="font-mono text-xs break-all">{run.k8sJobName || "—"}</span>,
              },
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
            <CardTitle className="flex items-center gap-2 text-base">
              <ScrollIcon className="size-4" />
              Logs
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-muted-foreground text-sm">
              Task run output is stored on the pod. Open the app console for the full log tail.
            </p>
            <div>
              <Button asChild size="sm" variant="outline">
                <Link href={consoleHref}>Open logs in console</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
