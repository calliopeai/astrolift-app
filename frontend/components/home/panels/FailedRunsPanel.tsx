"use client";

/**
 * Failed runs (spec 44 §4.3): the newest failed agent and workflow runs,
 * each with its reason, as a ListSummary to Runs' Failed view.
 */

import { CircleXIcon, GitBranchIcon, PlayIcon } from "lucide-react";
import * as React from "react";

import { ListSummary } from "@/components/list/ListSummary";
import { StatusDot } from "@/components/StatusDot";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import { newestFirst } from "./apps-agents-model";
import type { HomeRead } from "./home-reads";
import { useFailedRuns } from "./use-failed-runs";

export interface FailedRunItem {
  key: string;
  kind: "agent" | "workflow";
  /** What ran: the agent, the workflow. */
  subject: string;
  /** The run's id, in mono. */
  id: string;
  reason: string;
  /** ISO time it ended. */
  at: string;
  href: string;
}

export interface FailedRunsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  items: FailedRunItem[];
  /** Every failed run, when each source can say; null when one cannot. */
  count: number | null;
}

const KIND_ICON: Record<FailedRunItem["kind"], React.ReactNode> = {
  agent: <PlayIcon className="size-3.5" />,
  workflow: <GitBranchIcon className="size-3.5" />,
};

function FailedRunLine({ item }: { item: FailedRunItem }) {
  const { t, age } = useHomePresentation(true);
  return (
    <div className="min-w-0">
      <p className="flex min-w-0 items-center gap-2">
        <StatusDot status="error" />
        <span className="text-muted-foreground shrink-0" aria-hidden>
          {KIND_ICON[item.kind]}
        </span>
        <span className="sr-only">
          {t(item.kind === "agent" ? "copy.agentRun" : "copy.workflowRun")}:{" "}
        </span>
        <span className="min-w-0 flex-1 truncate font-medium" title={item.subject}>
          {item.subject}
        </span>
        <span className="text-muted-foreground shrink-0 font-mono text-xs">{age(item.at)}</span>
      </p>
      <p
        className="text-muted-foreground mt-0.5 truncate font-mono text-xs"
        title={`${item.id}: ${item.reason}`}
      >
        {item.reason}
      </p>
    </div>
  );
}

/** Pure. */
export function FailedRunsPanelView({
  panel,
  items,
  count,
  loading,
  error,
  onRetry,
}: FailedRunsPanelViewProps) {
  const { t } = useHomePresentation();
  return (
    <ListSummary
      title={homePanelTitle(panel, t)}
      icon={<CircleXIcon className="size-4" />}
      span={panel.span}
      count={loading || error ? null : count}
      rows={newestFirst(items, (i) => i.at)}
      keyOf={(i) => i.key}
      renderRow={(i) => <FailedRunLine item={i} />}
      rowHref={(i) => i.href}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <CircleXIcon />,
        title: t("copy.noFailedRunsTitle"),
        description: t("copy.noFailedRunsDescription"),
      }}
    />
  );
}

/** Registered on Home as `failed-runs`. */
export function FailedRunsPanel({ panel }: HomePanelProps) {
  return <FailedRunsPanelView panel={panel} {...useFailedRuns()} />;
}
