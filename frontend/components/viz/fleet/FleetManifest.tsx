"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { EVENT_WINDOW_MS, type FleetRun, type FleetViewProps } from "../core/fleet-model";
import { HEALTH_COLOR, MOTION_CLASS } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import {
  RUN_STATE_GLOWS,
  RUN_STATE_HEALTH,
  RUN_STATE_LABEL,
  boardOrder,
  runClock,
  runDetail,
  stateSince,
  summarizeRuns,
} from "./manifest";

/**
 * Launch manifest (spec 44 viz addendum): the run queue as a departures
 * board. Rows sit in scheduled order and stay put; what moves is the status.
 *
 * Motion, and what it means:
 *   - a status cell flips (split-flap) once when that run changes state;
 *   - a holding run breathes amber while it waits on its hold reason.
 * Reduced motion: no flip and no breathing. A run that changed state inside
 * the event window carries a status-coloured rule on its left edge instead,
 * and holding stays amber with its reason written out.
 */

export const FLEET_MANIFEST_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Queued, T-minus to lift-off" },
  { glyph: "ring", color: HEALTH_COLOR.degraded, label: "Holding (breathes while it waits)" },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Lifted off / in flight" },
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Landed" },
  { glyph: "glow", color: HEALTH_COLOR.failing, label: "Failed" },
  {
    glyph: "signal",
    color: "var(--foreground)",
    label: "Status flips once when a run changes state",
  },
];

const GRID =
  "grid grid-cols-[5.5rem_minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)] items-center gap-x-3";

const FLIP_MS = 280;

function glow(color: string): string {
  return `0 0 8px color-mix(in oklab, ${color} 55%, transparent)`;
}

/**
 * The status word. Keyed by run id + state, so a state change mounts a new
 * cell; on mount it flips if this run was on the board before in another
 * state. `seen` holds the states from the previous render: child effects run
 * before the parent's, so it still has the old value here.
 */
function StatusFlap({
  run,
  now,
  motion,
  seen,
}: {
  run: FleetRun;
  now: number;
  motion: FleetViewProps["motion"];
  seen: React.RefObject<Map<string, FleetRun["state"]>>;
}) {
  const ref = React.useRef<HTMLSpanElement>(null);
  React.useLayoutEffect(() => {
    const el = ref.current;
    const before = seen.current.get(run.id);
    if (motion !== "full" || !el || before === undefined || before === run.state) return;
    if (typeof el.animate !== "function") return;
    const anim = el.animate(
      [
        { transform: "perspective(240px) rotateX(-90deg)", opacity: 0.4 },
        { transform: "perspective(240px) rotateX(0deg)", opacity: 1 },
      ],
      { duration: FLIP_MS, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" }
    );
    return () => anim.cancel();
    // Mount-only: the key changes with the state, so this runs once per state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const health = RUN_STATE_HEALTH[run.state];
  const color = HEALTH_COLOR[health];
  const detail = runDetail(run, now);
  return (
    <span
      ref={ref}
      className="inline-flex min-w-0 origin-center items-center gap-2"
      data-state={run.state}
    >
      <span
        aria-hidden
        className={cn(
          "size-2 shrink-0 rounded-full",
          run.state === "holding" && MOTION_CLASS.breathe
        )}
        style={
          run.state === "holding"
            ? { boxShadow: `inset 0 0 0 1.5px ${color}` }
            : {
                background: color,
                boxShadow: RUN_STATE_GLOWS[run.state] ? glow(color) : undefined,
              }
        }
      />
      <span
        className={cn(
          "shrink-0 font-mono text-xs font-medium tracking-wide uppercase",
          run.state === "holding" && MOTION_CLASS.breathe
        )}
        style={{
          color,
          textShadow: RUN_STATE_GLOWS[run.state] ? glow(color) : undefined,
        }}
      >
        {RUN_STATE_LABEL[run.state]}
      </span>
      {detail && (
        <span
          className={cn(
            "text-muted-foreground min-w-0 truncate text-xs",
            run.state === "in_flight" && "font-mono"
          )}
          title={detail}
        >
          {detail}
        </span>
      )}
    </span>
  );
}

function Truncated({ text, className }: { text: string; className?: string }) {
  return (
    <span role="cell" className={cn("min-w-0 truncate", className)} title={text}>
      {text}
    </span>
  );
}

export function FleetManifest({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const { now, runs } = snapshot;
  const agents = React.useMemo(
    () => new Map(snapshot.agents.map((a) => [a.id, a])),
    [snapshot.agents]
  );
  const clusters = React.useMemo(
    () => new Map(snapshot.clusters.map((c) => [c.id, c])),
    [snapshot.clusters]
  );
  const ordered = React.useMemo(() => boardOrder(runs), [runs]);

  // Each run's state as of the last render, for the flip (see StatusFlap).
  const seen = React.useRef(new Map<string, FleetRun["state"]>());
  React.useEffect(() => {
    seen.current = new Map(runs.map((r) => [r.id, r.state]));
  }, [runs]);

  const summary = summarizeRuns(runs);

  return (
    <div
      data-motion={motion}
      role="group"
      aria-label={`Launch manifest. ${summary}`}
      className={cn("bg-card w-full min-w-0 text-sm", className)}
    >
      <div role="table" aria-label="Run queue" aria-rowcount={ordered.length + 1}>
        <div role="rowgroup">
          <div
            role="row"
            className={cn(
              GRID,
              "text-muted-foreground text-2xs border-b px-3 py-2 font-mono tracking-widest uppercase"
            )}
          >
            <span role="columnheader">T- / UTC</span>
            <span role="columnheader">Run</span>
            <span role="columnheader">Agent</span>
            <span role="columnheader">Cluster</span>
            <span role="columnheader">Status</span>
          </div>
        </div>
        <div role="rowgroup" className="max-h-[32rem] overflow-y-auto">
          {ordered.length === 0 && (
            <div role="row" className="text-muted-foreground px-3 py-6 text-center text-xs">
              <span role="cell">No runs on the board</span>
            </div>
          )}
          {ordered.map((run) => {
            const agent = agents.get(run.agentId);
            const cluster = agent ? clusters.get(agent.clusterId) : undefined;
            const selected = selectedAgentId !== undefined && run.agentId === selectedAgentId;
            const since = stateSince(run);
            const fresh =
              motion === "reduced" && since !== undefined && now - since < EVENT_WINDOW_MS;
            const color = HEALTH_COLOR[RUN_STATE_HEALTH[run.state]];
            const select = () => onSelectAgent?.(run.agentId);
            return (
              <div
                key={run.id}
                role="row"
                tabIndex={0}
                data-selected={selected || undefined}
                onClick={select}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    select();
                  }
                }}
                className={cn(
                  GRID,
                  "border-border/60 cursor-pointer border-b border-l-2 border-l-transparent px-3 py-1.5 outline-none last:border-b-0",
                  "hover:bg-muted/40 focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-inset",
                  selected && "bg-muted/60"
                )}
                style={fresh ? { borderLeftColor: color } : undefined}
              >
                <span
                  role="cell"
                  className={cn(
                    "font-mono text-xs tabular-nums",
                    run.state === "queued" || run.state === "holding"
                      ? "text-foreground"
                      : "text-muted-foreground"
                  )}
                >
                  {runClock(run, now)}
                </span>
                <Truncated text={run.label} className="font-medium" />
                <Truncated text={agent?.name ?? run.agentId} className="font-mono text-xs" />
                <Truncated
                  text={cluster?.name ?? "unknown"}
                  className="text-muted-foreground font-mono text-xs"
                />
                <span role="cell" className="min-w-0 overflow-hidden">
                  <StatusFlap
                    key={`${run.id}:${run.state}`}
                    run={run}
                    now={now}
                    motion={motion}
                    seen={seen}
                  />
                  {selected && <span className="sr-only">, selected agent</span>}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
