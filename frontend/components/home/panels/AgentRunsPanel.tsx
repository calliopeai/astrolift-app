"use client";

/**
 * Agent runs (spec 44 §4.3, Builder): the five newest agent runs of every
 * status, as a ListSummary to Agents › Runs. Each line is the run's short
 * id, the agent, its outcome and when it started.
 */

import { PlayIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import {
  outcomeOf,
  type RunOutcome,
} from "@/components/screens/administration/insights/combined-runs";
import { StatusDot } from "@/components/StatusDot";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import { agentRunHref } from "./apps-agents-model";
import type { HomeAgentTask, HomeRead } from "./home-reads";
import { useAgentRunsPanel } from "./use-home-panels";

export interface AgentRunsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  rows: HomeAgentTask[];
  count: number | null;
}

const OUTCOME_DOT: Record<RunOutcome, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  waiting: "warn",
  succeeded: "ok",
  failed: "error",
  cancelled: "muted",
  unknown: "muted",
};

function RunLine({ run }: { run: HomeAgentTask }) {
  const { age, status } = useHomePresentation(true);
  const outcome = outcomeOf("agent", run.status);
  return (
    <span className="flex min-w-0 items-center gap-3">
      <span className="w-12 shrink-0 font-mono text-xs" title={run.id}>
        {run.id.slice(0, 4)}
      </span>
      <span className="min-w-0 flex-1 truncate" title={run.agentName || run.agentSlug}>
        {run.agentSlug || run.agentName}
      </span>
      <span className="min-w-0 shrink-0">
        <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-xs">
          <StatusDot status={OUTCOME_DOT[outcome]} />
          <span>{status(outcome)}</span>
          {run.status && run.status !== outcome && (
            <span className="text-muted-foreground truncate" title={run.status}>
              · {status(run.status)}
            </span>
          )}
        </span>
      </span>
      <span className="text-muted-foreground w-16 shrink-0 text-right font-mono text-xs">
        {age(run.startedAt ?? run.createdAt)}
      </span>
    </span>
  );
}

/** Pure. */
export function AgentRunsPanelView({
  panel,
  rows,
  count,
  loading,
  error,
  onRetry,
}: AgentRunsPanelViewProps) {
  const { t } = useHomePresentation();
  return (
    <ListSummary
      title={homePanelTitle(panel, t)}
      icon={<PlayIcon className="size-4" />}
      span={panel.span}
      count={loading || error ? null : count}
      rows={rows}
      keyOf={(r) => r.id}
      renderRow={(r) => <RunLine run={r} />}
      rowHref={(r) => agentRunHref(r.id)}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <PlayIcon />,
        title: t("copy.noAgentRunsTitle"),
        description: t("copy.noAgentRunsDescription"),
        actionHref: "/agents",
        actionLabel: t("copy.openAgents"),
      }}
    />
  );
}

/** Registered on Home as `agent-runs`. */
export function AgentRunsPanel({ panel }: HomePanelProps) {
  return <AgentRunsPanelView panel={panel} {...useAgentRunsPanel()} />;
}
