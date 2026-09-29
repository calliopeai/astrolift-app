"use client";

import { ClipboardListIcon, InfoIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";

import { DetailTimestamp } from "@/components/detail/EntityDetailShell";
import { Identifier } from "@/components/Identifier";
import type { RunStatus } from "@/components/jobs/RunStatusBadge";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { RunPage } from "@/components/run/RunPage";
import { commandText, runElapsed, runSteps } from "@/components/screens/jobs/jobs-list";
import { RunMenu, RunMissing } from "@/components/screens/jobs/RunDetailParts";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";

import { fromTaskRun, runCrumbs } from "./runs-list";
import { RunOutcomeCell } from "./RunsScreen";
import type { useTaskRunDetail } from "./use-task-run-detail";

export type TaskRunDetailProps = ReturnType<typeof useTaskRunDetail> & { id: string };

/** Agents ▾ › Runs › billing › run a1b2c3d4 (spec 44 §4.4). */
function crumbs(id: string, run: AstroliftTaskRun | null) {
  const last = { label: `run ${id.slice(0, 8)}` };
  return run
    ? runCrumbs(
        {
          label: run.registeredAppSlug,
          href: `/apps/${encodeURIComponent(run.registeredAppSlug)}`,
        },
        last
      )
    : runCrumbs(last);
}

/** The Timeline's vocabulary for a task run's outcome. */
const STEP_STATUS: Record<string, RunStatus> = {
  running: "running",
  succeeded: "succeeded",
  failed: "failed",
  cancelled: "superseded",
};

/**
 * One container task run on the run archetype (spec 44 §5.5, #1106,
 * #1118): waiting for a pod, then the run, on the left; the log on the
 * right, which for a task run is a link to the app console (task runs
 * carry no inline output); a failure's exit code first. Pure view; the
 * data half is useTaskRunDetail.
 */
export function TaskRunDetail({ id, loading, run, error, onRetry, now }: TaskRunDetailProps) {
  if (!run && loading) {
    return (
      <RunPage
        crumbs={crumbs(id, null)}
        title={`run ${id.slice(0, 8)}`}
        steps={[]}
        stepsLoading
        log={{ lines: [], loading: true }}
      />
    );
  }
  if (!run) {
    return (
      <RunMissing
        crumbs={crumbs(id, null)}
        title="Task run"
        icon={<ClipboardListIcon className="size-4" />}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ClipboardListIcon className="size-5" />,
          title: "Task run not found",
          description: "No task run has this id, or you do not have permission to see it.",
          actionHref: "/tasks?kind=task",
          actionLabel: "Open task runs",
        }}
      />
    );
  }

  const row = fromTaskRun(run, null);
  const live = row.outcome === "running" || row.outcome === "waiting";
  const status = STEP_STATUS[run.status] ?? "unknown";
  const command = commandText(run.command);
  const appHref = `/apps/${encodeURIComponent(run.registeredAppSlug)}`;
  const workloadHref = `${appHref}/workloads/${encodeURIComponent(run.workloadSlug)}`;
  const consoleHref = `${appHref}/logs?${new URLSearchParams({
    workload: run.workloadSlug,
    run: run.k8sJobName || run.id,
  }).toString()}`;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <RunPage
        crumbs={crumbs(id, run)}
        title={`run ${id.slice(0, 8)}`}
        status={<RunOutcomeCell run={row} />}
        durationMs={runElapsed(run, live, now)}
        context={
          <span className="font-mono">
            {run.triggerKind}
            {run.triggeredByUsername ? ` · ${run.triggeredByUsername}` : ""}
          </span>
        }
        primaryAction={
          <Button size="sm" variant="outline" asChild>
            <Link href={consoleHref}>
              <ScrollIcon className="size-4" />
              Open logs
            </Link>
          </Button>
        }
        menu={
          <RunMenu
            id={run.id}
            links={[
              { label: "Open app", href: appHref },
              { label: "Open workload", href: workloadHref },
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
        failure={
          run.status === "failed"
            ? {
                title: "Run failed",
                reason: run.exitCode != null ? `exit ${run.exitCode}` : "failed",
              }
            : null
        }
        log={{
          lines: [],
          emptyHint: (
            <>
              Task run output is stored on the pod.{" "}
              <Link href={consoleHref} className="not-italic underline underline-offset-2">
                Open the app console
              </Link>{" "}
              for the full log tail.
            </>
          ),
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
                  <Link href={appHref} className="font-mono text-xs hover:underline">
                    {run.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: "Workload",
                description: (
                  <Link href={workloadHref} className="font-mono text-xs hover:underline">
                    {run.workloadSlug}
                  </Link>
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
              {
                term: "Exit code",
                description: <span className="font-mono">{run.exitCode ?? "—"}</span>,
              },
              {
                term: "K8s job",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {run.k8sJobName || "—"}
                  </span>
                ),
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
