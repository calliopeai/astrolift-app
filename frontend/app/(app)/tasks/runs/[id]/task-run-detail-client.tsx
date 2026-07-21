"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import { ScrollIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DetailStatusBadge, DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { GET_TASK_RUN, LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";

interface TaskRunResp {
  astroliftTaskRun: AstroliftTaskRun | null;
}

interface TaskRunListResp {
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
 * Task run detail (#1106, #1118). Prefers the singular `astroliftTaskRun(id)`
 * query so a cold deep-link to a run outside the 100-row list window still
 * resolves. Falls back to the row already in the cached LIST_TASK_RUNS window
 * for an instant paint when navigated from /tasks (cache hit); the by-id fetch
 * refreshes in the background and is the source of truth otherwise.
 */
export function TaskRunDetailClient({ id }: { id: string }) {
  const client = useApolloClient();
  const { data, loading } = useQuery<TaskRunResp>(GET_TASK_RUN, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });

  const cachedFromList = React.useMemo(() => {
    const listed = client.readQuery<TaskRunListResp>({
      query: LIST_TASK_RUNS,
      variables: { limit: 100 },
    });
    return listed?.astroliftTaskRuns.find((r) => r.id === id) ?? null;
  }, [client, id]);

  const run = data?.astroliftTaskRun ?? cachedFromList;

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
