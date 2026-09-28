"use client";

import { ScrollIcon } from "lucide-react";
import Link from "next/link";

import {
  DetailStatusBadge,
  DetailTimestamp,
  EntityDetailShell,
} from "@/components/detail/EntityDetailShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { formatDuration } from "@/components/screens/jobs/jobs-format";

import type { useTaskRunDetail } from "./use-task-run-detail";

export type TaskRunDetailProps = ReturnType<typeof useTaskRunDetail> & { id: string };

/**
 * Task run detail (#1106, #1118). Task runs carry no inline `output`, so
 * the Logs card deep-links into the app console. Data comes from
 * useTaskRunDetail.
 */
export function TaskRunDetail({ id, loading, run }: TaskRunDetailProps) {
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
                description: (
                  <span className="font-mono text-xs break-all">{run.k8sJobName || "—"}</span>
                ),
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
