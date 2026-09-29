"use client";

/**
 * Agent runs (spec 44 §4.3, Builder): the five newest agent runs of every
 * status, as a ListSummary to Agents › Runs. Each line is the run's short
 * id, the agent, its outcome and when it started.
 */

import { PlayIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import { outcomeOf } from "@/components/screens/administration/insights/combined-runs";
import { RunOutcomeCell } from "@/components/screens/tasks/RunsScreen";
import { formatRelativeAge } from "@/lib/format";

import type { HomePanelProps } from "../registry";
import { agentRunHref } from "./apps-agents-model";
import type { HomeAgentTask, HomeRead } from "./home-reads";
import { useAgentRunsPanel } from "./use-home-panels";

export interface AgentRunsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  rows: HomeAgentTask[];
  count: number | null;
}

function RunLine({ run }: { run: HomeAgentTask }) {
  return (
    <span className="flex min-w-0 items-center gap-3">
      <span className="w-12 shrink-0 font-mono text-xs" title={run.id}>
        {run.id.slice(0, 4)}
      </span>
      <span className="min-w-0 flex-1 truncate" title={run.agentName || run.agentSlug}>
        {run.agentSlug || run.agentName}
      </span>
      <span className="min-w-0 shrink-0">
        <RunOutcomeCell run={{ outcome: outcomeOf("agent", run.status), status: run.status }} />
      </span>
      <span className="text-muted-foreground w-16 shrink-0 text-right font-mono text-xs">
        {formatRelativeAge(run.startedAt ?? run.createdAt)}
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
  return (
    <ListSummary
      title={panel.title}
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
        title: "No agent runs yet",
        description: "Runs of the agents you can see show up here.",
        actionHref: "/agents",
        actionLabel: "Open agents",
      }}
    />
  );
}

/** Registered on Home as `agent-runs`. */
export function AgentRunsPanel({ panel }: HomePanelProps) {
  return <AgentRunsPanelView panel={panel} {...useAgentRunsPanel()} />;
}
