"use client";

/**
 * Recent deployments (spec 44 §4.3): the five newest deploys the viewer
 * can see, as a ListSummary to Apps › Deployments.
 */

import { RocketIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import { StatusDot } from "@/components/StatusDot";
import { formatRelativeAge } from "@/lib/format";

import type { HomePanelProps } from "../registry";
import { deployDot, deployHref, statusLabel } from "./apps-agents-model";
import type { HomeRead } from "./home-reads";
import { useRecentDeployments } from "./use-recent-deployments";

export interface RecentDeployItem {
  id: string;
  /** The commit or id, eight characters, in mono. */
  short: string;
  app: string;
  environment: string;
  status: string;
  /** ISO time it started. */
  at: string;
}

export interface RecentDeploymentsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  items: RecentDeployItem[];
  count: number | null;
}

function DeployLine({ item }: { item: RecentDeployItem }) {
  return (
    <span className="flex min-w-0 items-center gap-3">
      <StatusDot status={deployDot(item.status)} />
      <span className="w-16 shrink-0 font-mono text-xs" title={item.id}>
        {item.short}
      </span>
      <span className="min-w-0 flex-1 truncate" title={`${item.app} · ${item.environment}`}>
        {item.app}
        <span className="text-muted-foreground font-mono text-xs"> · {item.environment}</span>
      </span>
      <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">
        {statusLabel(item.status)}
      </span>
      <span className="text-muted-foreground w-16 shrink-0 text-right font-mono text-xs">
        {formatRelativeAge(item.at)}
      </span>
    </span>
  );
}

/** Pure. */
export function RecentDeploymentsPanelView({
  panel,
  items,
  count,
  loading,
  error,
  onRetry,
}: RecentDeploymentsPanelViewProps) {
  return (
    <ListSummary
      title={panel.title}
      icon={<RocketIcon className="size-4" />}
      span={panel.span}
      count={count}
      rows={items}
      keyOf={(d) => d.id}
      renderRow={(d) => <DeployLine item={d} />}
      rowHref={(d) => deployHref(d.id)}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <RocketIcon />,
        title: "No deployments yet",
        description: "Deploys you can see show up here.",
        actionHref: "/deployments/new",
        actionLabel: "Start a deployment",
      }}
    />
  );
}

/** Registered on Home as `recent-deployments`. */
export function RecentDeploymentsPanel({ panel }: HomePanelProps) {
  return <RecentDeploymentsPanelView panel={panel} {...useRecentDeployments()} />;
}
