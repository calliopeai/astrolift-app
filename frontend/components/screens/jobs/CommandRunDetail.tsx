"use client";

import { InfoIcon, ScrollIcon, TerminalIcon } from "lucide-react";
import Link from "next/link";

import { DetailTimestamp } from "@/components/detail/EntityDetailShell";
import { Identifier } from "@/components/Identifier";
import { RunStatusBadge } from "@/components/jobs/RunStatusBadge";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { RunPage } from "@/components/run/RunPage";
import { appsDetailCrumbs } from "@/components/screens/deployments/apps-area";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import type { AstroliftCommandRun } from "@/graphql/lifecycle/lifecycle.types";

import {
  commandStatus,
  commandText,
  outputLines,
  runElapsed,
  runFailure,
  runSteps,
} from "./jobs-list";
import { RunMenu, RunMissing } from "./RunDetailParts";
import type { useCommandRunDetail } from "./use-command-run-detail";

export type CommandRunDetailProps = ReturnType<typeof useCommandRunDetail> & { id: string };

/** Scheduled jobs ▾ › Commands › billing › command 8b7c6d5e (spec 44 §4.4). */
function crumbs(id: string, run: AstroliftCommandRun | null) {
  const last = { label: `command ${id.slice(0, 8)}` };
  const commands = { label: "Commands", href: "/jobs/commands" };
  if (!run) return appsDetailCrumbs("jobs", commands, last);
  return appsDetailCrumbs(
    "jobs",
    commands,
    {
      label: run.registeredAppSlug,
      href: `/jobs/commands?app=${encodeURIComponent(run.registeredAppSlug)}`,
    },
    last
  );
}

/**
 * One command run on the run archetype (spec 44 §5.5, #1106). A command run
 * carries no status string: it is derived from its exit code and end time,
 * as on the commands list. Pure view; the data half is useCommandRunDetail.
 */
export function CommandRunDetail({
  id,
  loading,
  run,
  error,
  onRetry,
  now,
  onDownload,
}: CommandRunDetailProps) {
  if (!run && loading) {
    return (
      <RunPage
        crumbs={crumbs(id, null)}
        title={`command ${id.slice(0, 8)}`}
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
        title="Command run"
        icon={<TerminalIcon className="size-4" />}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <TerminalIcon className="size-5" />,
          title: "Command run not found",
          description: "No command run has this id, or you do not have permission to see it.",
          actionHref: "/jobs/commands",
          actionLabel: "Open command runs",
        }}
      />
    );
  }

  const status = commandStatus(run);
  const live = status === "in_progress";
  const command = commandText(run.command);
  const output = run.output ?? "";
  const logsHref = `/apps/${run.registeredAppSlug}/logs?${new URLSearchParams(
    run.workloadSlug ? { command: run.id, workload: run.workloadSlug } : { command: run.id }
  ).toString()}`;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <RunPage
        crumbs={crumbs(id, run)}
        title={`command ${id.slice(0, 8)}`}
        status={<RunStatusBadge status={status} exitCode={run.exitCode} />}
        durationMs={runElapsed(run, live, now)}
        context={run.invokedByUsername ? `by ${run.invokedByUsername}` : undefined}
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
              ...(run.workloadSlug
                ? [
                    {
                      label: "Open workload",
                      href: `/apps/${run.registeredAppSlug}/workloads/${run.workloadSlug}`,
                    },
                  ]
                : []),
            ]}
          />
        }
        steps={runSteps(
          run,
          status,
          command || "command",
          now,
          run.exitCode != null ? `exit ${run.exitCode}` : undefined
        )}
        failure={runFailure(status, run.exitCode, output, run.logExcerpt ?? "")}
        log={{
          title: "Output",
          lines: outputLines(output, run.startedAt ?? run.createdAt),
          onDownload: output ? onDownload : undefined,
          emptyHint: live ? "No output yet." : "This command wrote no output.",
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
                term: "Workload",
                description: run.workloadSlug ? (
                  <Link
                    href={`/apps/${run.registeredAppSlug}/workloads/${run.workloadSlug}`}
                    className="font-mono text-xs hover:underline"
                  >
                    {run.workloadSlug}
                  </Link>
                ) : (
                  "—"
                ),
              },
              {
                term: "Command",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {command || "—"}
                  </span>
                ),
              },
              { term: "Invoked by", description: run.invokedByUsername ?? "—" },
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
