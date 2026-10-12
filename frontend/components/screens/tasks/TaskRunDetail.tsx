"use client";

import { ClipboardListIcon, InfoIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

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
function crumbs(
  id: string,
  run: AstroliftTaskRun | null,
  t: ReturnType<typeof useTranslations<"runPages">>
) {
  const last = { label: t("runTitle", { id: id.slice(0, 8) }) };
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
  const t = useTranslations("runPages");
  if (!run && loading) {
    return (
      <RunPage
        crumbs={crumbs(id, null, t)}
        title={t("runTitle", { id: id.slice(0, 8) })}
        steps={[]}
        stepsLoading
        log={{ lines: [], loading: true }}
      />
    );
  }
  if (!run) {
    return (
      <RunMissing
        crumbs={crumbs(id, null, t)}
        title={t("taskRun")}
        icon={<ClipboardListIcon className="size-4" />}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ClipboardListIcon className="size-5" />,
          title: t("taskRunNotFound"),
          description: t("taskRunNotFoundDescription"),
          actionHref: "/tasks?kind=task",
          actionLabel: t("openTaskRuns"),
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
        crumbs={crumbs(id, run, t)}
        title={t("runTitle", { id: id.slice(0, 8) })}
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
              {t("openLogs")}
            </Link>
          </Button>
        }
        menu={
          <RunMenu
            id={run.id}
            links={[
              { label: t("openApp"), href: appHref },
              { label: t("openWorkload"), href: workloadHref },
            ]}
          />
        }
        steps={runSteps(
          run,
          status,
          run.k8sJobName || run.workloadSlug,
          now,
          run.exitCode != null ? t("exit", { code: run.exitCode }) : undefined
        )}
        failure={
          run.status === "failed"
            ? {
                title: t("runFailed"),
                reason: run.exitCode != null ? t("exit", { code: run.exitCode }) : t("failed"),
              }
            : null
        }
        log={{
          lines: [],
          emptyHint: (
            <>
              {t.rich("taskOutputHint", {
                link: (chunks) => (
                  <Link href={consoleHref} className="not-italic underline underline-offset-2">
                    {chunks}
                  </Link>
                ),
              })}
            </>
          ),
        }}
      />

      <PanelGrid>
        <Panel title={t("details")} icon={<InfoIcon className="size-4" />} span={6}>
          <DefinitionList
            items={[
              { term: t("runId"), description: <Identifier value={run.id} form="full" /> },
              {
                term: t("app"),
                description: (
                  <Link href={appHref} className="font-mono text-xs hover:underline">
                    {run.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: t("workload"),
                description: (
                  <Link href={workloadHref} className="font-mono text-xs hover:underline">
                    {run.workloadSlug}
                  </Link>
                ),
              },
              {
                term: t("command"),
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {command || "—"}
                  </span>
                ),
              },
              {
                term: t("exitCode"),
                description: <span className="font-mono">{run.exitCode ?? "—"}</span>,
              },
              {
                term: t("k8sJob"),
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {run.k8sJobName || "—"}
                  </span>
                ),
              },
              { term: t("created"), description: <DetailTimestamp iso={run.createdAt} /> },
              { term: t("started"), description: <DetailTimestamp iso={run.startedAt} /> },
              { term: t("ended"), description: <DetailTimestamp iso={run.endedAt} /> },
            ]}
          />
        </Panel>
      </PanelGrid>
    </div>
  );
}
