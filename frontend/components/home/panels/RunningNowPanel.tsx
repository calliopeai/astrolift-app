"use client";

/**
 * Running now (spec 44 §4.3; the viz rule): the agents with a run in
 * flight, drawn in the viewer's own fleet view (their saved FleetView
 * style, with its switcher and legend), sized to the panel's column. Until
 * something runs, it is a Panel with its empty state.
 */

import { ActivityIcon } from "lucide-react";

import type { PanelSpan } from "@/components/panel/Panel";
import { Panel } from "@/components/panel/Panel";
import { FleetView } from "@/components/viz/FleetView";
import type { FleetSnapshot } from "@/components/viz/core/fleet-model";
import { cn } from "@/lib/utils";

import type { HomePanelProps } from "../registry";
import type { HomeRead } from "./home-reads";
import { useRunningNow } from "./use-running-now";

// Panel's own column classes, for the fleet frame that stands in its place.
const SPAN: Record<PanelSpan, string> = {
  12: "col-span-12",
  9: "col-span-12 xl:col-span-9",
  8: "col-span-12 xl:col-span-8",
  6: "col-span-12 xl:col-span-6",
  4: "col-span-12 xl:col-span-4",
  3: "col-span-12 lg:col-span-6 xl:col-span-3",
};

export interface RunningNowPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  /** Null until the fleet answers. */
  snapshot: FleetSnapshot | null;
  onSelectAgent?: (agentId: string) => void;
}

/** Pure. */
export function RunningNowPanelView({
  panel,
  snapshot,
  loading,
  error,
  onRetry,
  onSelectAgent,
}: RunningNowPanelViewProps) {
  if (loading || error || !snapshot || snapshot.agents.length === 0) {
    return (
      <Panel
        title={panel.title}
        icon={<ActivityIcon className="size-4" />}
        span={panel.span}
        loading={loading}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ActivityIcon />,
          title: "Nothing running",
          description: "Agents with a run in flight show up here, in your fleet view.",
          actionHref: panel.href,
          actionLabel: "Open runs",
        }}
      />
    );
  }
  const runs = snapshot.agents.reduce((n, a) => n + a.activeRuns, 0);
  return (
    <div className={cn("min-w-0", SPAN[panel.span])}>
      <FleetView
        snapshot={snapshot}
        onSelectAgent={onSelectAgent}
        title={panel.title}
        description={
          <span className="font-mono">
            {runs} {runs === 1 ? "run" : "runs"} on {snapshot.agents.length}{" "}
            {snapshot.agents.length === 1 ? "agent" : "agents"}
          </span>
        }
        className="h-full min-w-0"
      />
    </div>
  );
}

/** Registered on Home as `running-now`. */
export function RunningNowPanel({ panel }: HomePanelProps) {
  return <RunningNowPanelView panel={panel} {...useRunningNow()} />;
}
