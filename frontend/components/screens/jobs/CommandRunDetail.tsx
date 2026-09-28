"use client";

import Link from "next/link";

import {
  DetailStatusBadge,
  DetailTimestamp,
  EntityDetailShell,
} from "@/components/detail/EntityDetailShell";
import { RunOutputPanel } from "@/components/jobs/RunOutputPanel";
import { commandRunStatus } from "@/components/jobs/RunStatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { useCommandRunDetail } from "./use-command-run-detail";

export type CommandRunDetailProps = ReturnType<typeof useCommandRunDetail> & { id: string };

/**
 * Command (one-off exec) run detail (#1106, #1118). Command runs carry no
 * explicit status string — it's derived from exit code + ended-at, same as the
 * /jobs Commands table. Data comes from useCommandRunDetail.
 */
export function CommandRunDetail({ id, loading, run }: CommandRunDetailProps) {
  const command = run
    ? Array.isArray(run.command)
      ? (run.command as string[]).join(" ")
      : String(run.command ?? "")
    : "";

  const status = run
    ? commandRunStatus({ endedAt: run.endedAt, exitCode: run.exitCode })
    : undefined;

  const consoleHref = run
    ? `/apps/${run.registeredAppSlug}/logs?${new URLSearchParams(
        run.workloadSlug ? { command: run.id, workload: run.workloadSlug } : { command: run.id }
      ).toString()}`
    : "#";

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!run}
      breadcrumb={{ label: "Jobs", href: "/jobs?tab=commands" }}
      heading={`Command ${id.slice(0, 8)}`}
      status={status}
      createdAt={run?.createdAt}
      notFoundLabel="command run"
      overview={
        run
          ? [
              {
                term: "Status",
                description: <DetailStatusBadge status={status ?? "unknown"} />,
              },
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
                description: run.workloadSlug ? (
                  <Link
                    href={`/apps/${run.registeredAppSlug}/workloads/${run.workloadSlug}`}
                    className="font-mono text-xs text-[var(--brand-primary)] hover:underline"
                  >
                    {run.workloadSlug}
                  </Link>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              {
                term: "Command",
                description: <span className="font-mono text-xs break-all">{command || "—"}</span>,
              },
              { term: "Invoked by", description: run.invokedByUsername ?? "—" },
              {
                term: "Exit code",
                description:
                  run.exitCode == null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <span className="font-mono">{run.exitCode}</span>
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
            <CardTitle className="text-base">Output</CardTitle>
          </CardHeader>
          <CardContent>
            <RunOutputPanel
              output={run.output ?? ""}
              consoleHref={consoleHref}
              caption={command || undefined}
            />
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
