"use client";

import { InfoIcon, ScrollIcon, TerminalIcon } from "lucide-react";
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
function crumbs(
  id: string,
  run: AstroliftCommandRun | null,
  t: ReturnType<typeof useTranslations<"runPages">>
) {
  const last = { label: t("commandTitle", { id: id.slice(0, 8) }) };
  const commands = { label: t("commands"), href: "/jobs/commands" };
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
  const t = useTranslations("runPages");
  if (!run && loading) {
    return (
      <RunPage
        crumbs={crumbs(id, null, t)}
        title={t("commandTitle", { id: id.slice(0, 8) })}
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
        title={t("commandRun")}
        icon={<TerminalIcon className="size-4" />}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <TerminalIcon className="size-5" />,
          title: t("commandRunNotFound"),
          description: t("commandRunNotFoundDescription"),
          actionHref: "/jobs/commands",
          actionLabel: t("openCommandRuns"),
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
        crumbs={crumbs(id, run, t)}
        title={t("commandTitle", { id: id.slice(0, 8) })}
        status={<RunStatusBadge status={status} exitCode={run.exitCode} />}
        durationMs={runElapsed(run, live, now)}
        context={run.invokedByUsername ? t("by", { who: run.invokedByUsername }) : undefined}
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
              ...(run.workloadSlug
                ? [
                    {
                      label: t("openWorkload"),
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
          command || t("commandFallback"),
          now,
          run.exitCode != null ? t("exit", { code: run.exitCode }) : undefined
        )}
        failure={runFailure(status, run.exitCode, output, run.logExcerpt ?? "")}
        log={{
          title: t("output"),
          lines: outputLines(output, run.startedAt ?? run.createdAt),
          onDownload: output ? onDownload : undefined,
          emptyHint: live ? t("noOutputYet") : t("commandNoOutput"),
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
                term: t("workload"),
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
                term: t("command"),
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {command || "—"}
                  </span>
                ),
              },
              { term: t("invokedBy"), description: run.invokedByUsername ?? "—" },
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
