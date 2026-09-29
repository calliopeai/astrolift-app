"use client";

/**
 * My agents (spec 44 §4.3, decision 18): the viewer's agents, running
 * first, as a ListSummary to the Agents list's Mine view. While agents
 * record no owner, Mine is the agents on apps the viewer holds a role on,
 * and the panel says so under its title, in the Agents list's own words.
 */

import { BotIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import { StatusDot } from "@/components/StatusDot";
import { formatRelativeAge } from "@/lib/format";

import type { HomePanelProps } from "../registry";
import { agentHref, statusLabel } from "./apps-agents-model";
import type { HomeRead } from "./home-reads";
import { useMyAgents } from "./use-my-agents";

export interface MyAgentItem {
  slug: string;
  name: string;
  runningCount: number;
  paused: boolean;
  lastRunStatus: string | null;
  /** ISO time of the last run. */
  lastRunAt: string | null;
}

export interface MyAgentsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  items: MyAgentItem[];
  count: number | null;
  /** What Mine covers while it is a stand-in; shown under the title. */
  mineNote?: string;
}

const FAILED = new Set(["failed", "timed_out", "error"]);

function agentDot(a: MyAgentItem) {
  if (a.runningCount > 0) return "pending" as const;
  if (a.lastRunStatus && FAILED.has(a.lastRunStatus.toLowerCase())) return "error" as const;
  if (a.paused) return "muted" as const;
  return a.lastRunStatus ? ("ok" as const) : ("muted" as const);
}

function agentState(a: MyAgentItem): string {
  if (a.runningCount > 0) return `${a.runningCount} running`;
  if (a.paused) return "Paused";
  return a.lastRunStatus ? `Last run ${statusLabel(a.lastRunStatus).toLowerCase()}` : "Never run";
}

function MyAgentLine({ item }: { item: MyAgentItem }) {
  return (
    <span className="flex min-w-0 items-center gap-3">
      <StatusDot status={agentDot(item)} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium" title={item.name}>
          {item.name}
        </span>
        <span className="text-muted-foreground block truncate font-mono text-xs" title={item.slug}>
          {item.slug}
        </span>
      </span>
      <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">
        {agentState(item)}
      </span>
      <span className="text-muted-foreground w-16 shrink-0 text-right font-mono text-xs">
        {item.lastRunAt ? formatRelativeAge(item.lastRunAt) : ""}
      </span>
    </span>
  );
}

/** Running first, then the most recently run. Pure. */
export function orderAgents(items: MyAgentItem[]): MyAgentItem[] {
  const at = (a: MyAgentItem) => (a.lastRunAt ? Date.parse(a.lastRunAt) || 0 : 0);
  return [...items].sort(
    (a, b) =>
      Number(b.runningCount > 0) - Number(a.runningCount > 0) ||
      at(b) - at(a) ||
      a.name.localeCompare(b.name)
  );
}

/** Pure. */
export function MyAgentsPanelView({
  panel,
  items,
  count,
  mineNote,
  loading,
  error,
  onRetry,
}: MyAgentsPanelViewProps) {
  return (
    <ListSummary
      title={panel.title}
      icon={<BotIcon className="size-4" />}
      description={mineNote}
      span={panel.span}
      count={count}
      rows={orderAgents(items)}
      keyOf={(a) => a.slug}
      renderRow={(a) => <MyAgentLine item={a} />}
      rowHref={(a) => agentHref(a.slug)}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BotIcon />,
        title: "No agents of yours yet",
        description: "Agents on apps you hold a role on show up here.",
        actionHref: "/agents",
        actionLabel: "Browse agents",
      }}
    />
  );
}

/** Registered on Home as `my-agents`. */
export function MyAgentsPanel({ panel }: HomePanelProps) {
  return <MyAgentsPanelView panel={panel} {...useMyAgents()} />;
}
