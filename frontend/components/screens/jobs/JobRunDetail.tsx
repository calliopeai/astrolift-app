"use client";

import { HistoryIcon, InfoIcon, ScrollIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

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
function crumbs(
  id: string,
  run: AstroliftScheduledJobRun | null,
  t: ReturnType<typeof useTranslations<"runPages">>
) {
  const last = { label: t("runTitle", { id: id.slice(0, 8) }) };
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
  const t = useTranslations("runPages");
  if (!run && loading) {
    return (
      <RunPage
        crumbs={crumbs(id, null, t)}
        title={t("runTitle", { id: id.slice(0, 8) })}
        steps={[]}
        stepsLoading
        log={{ lines: [], loading: true, title: t("output") }}
      />
    );
  }
  if (!run) {
    return (
      <RunMissing
        crumbs={crumbs(id, null, t)}
        title={t("jobRun")}
        icon={<HistoryIcon className="size-4" />}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <HistoryIcon className="size-5" />,
          title: t("jobRunNotFound"),
          description: t("jobRunNotFoundDescription"),
          actionHref: "/jobs/runs",
          actionLabel: t("openJobRuns"),
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
        crumbs={crumbs(id, run, t)}
        title={t("runTitle", { id: id.slice(0, 8) })}
        status={<RunStatusBadge status={status} exitCode={run.exitCode} />}
        durationMs={runElapsed(run, live, now)}
        context={<span className="font-mono">{run.environmentName || "—"}</span>}
        primaryAction={
          <Button size="sm" variant="outline" asChild>
            <Link href={logsHref}>
              <ScrollIcon className="size-4" />
              {t("openLogs")}
            </Link>
          </Button>
        }
        menu={
          <RunMenu
            id={run.id}
            links={[
              { label: t("openApp"), href: `/apps/${run.registeredAppSlug}` },
              {
                label: t("openWorkload"),
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
          run.exitCode != null ? t("exit", { code: run.exitCode }) : undefined
        )}
        failure={runFailure(status, run.exitCode, output, run.logExcerpt ?? "")}
        log={{
          title: t("output"),
          lines: outputLines(output, run.startedAt ?? run.createdAt),
          onDownload: output ? onDownload : undefined,
          emptyHint: live ? t("noOutputYet") : t("runNoOutput"),
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
                  <Link href={`/apps/${run.registeredAppSlug}`} className="hover:underline">
                    {run.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: t("environment"),
                description: (
                  <span className="font-mono text-xs">{run.environmentName || "—"}</span>
                ),
              },
              {
                term: t("workload"),
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
                term: t("k8sJob"),
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {run.k8sJobName || "—"}
                  </span>
                ),
              },
              {
                term: t("exitCode"),
                description: <span className="font-mono">{run.exitCode ?? "—"}</span>,
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
