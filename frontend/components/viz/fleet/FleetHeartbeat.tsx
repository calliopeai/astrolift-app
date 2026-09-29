"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { EVENT_WINDOW_MS, type FleetCluster, type FleetViewProps } from "../core/fleet-model";
import { HEALTH_COLOR, HEALTH_GLOWS, HEALTH_LABEL, MOTION_CLASS } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import {
  layoutHeartbeat,
  rowGlowPercent,
  summarizeHeartbeat,
  type HeartbeatDot,
  type HeartbeatRow,
} from "./heartbeat-layout";

/**
 * Phosphor heartbeat wall (spec 44 viz addendum). One row per agent, grouped
 * by cluster. Time runs right to left over EVENT_WINDOW_MS: "now" is the right
 * edge, and every event lights a dot there that drifts left and fades like
 * phosphor until it is a window old. A busy row glows in its health colour,
 * brighter with load; an idle row stays dark.
 *
 * Reduced motion: no drift and no fade animation. Each dot is drawn at its
 * position for snapshot.now with a still opacity of 1 - age/window, so the
 * picture carries the same recency the animation would.
 */

export const FLEET_HEARTBEAT_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Run started or finished" },
  { glyph: "dot", color: HEALTH_COLOR.failing, label: "Run failed" },
  { glyph: "ring", color: "var(--foreground)", label: "Run dispatched" },
  { glyph: "flow", color: "var(--muted-foreground)", label: "Dots drift left and fade over 12s" },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Busy agent: row glows with load" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, label: "Degraded (overloaded)" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing agent" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle: row stays dark" },
];

const CLUSTER_COLOR: Record<FleetCluster["health"], string> = {
  ok: HEALTH_COLOR.ok,
  degraded: HEALTH_COLOR.degraded,
  offline: HEALTH_COLOR.failing,
};

const DRIFT_VAR = "--hb-drift";

/**
 * One event. The fade is a CSS animation started once per event id, with a
 * negative delay equal to its age at first paint, so an event that arrived
 * before mount is already partly faded. Inline opacity is the still frame;
 * a running animation overrides it.
 */
const Dot = React.memo(function Dot({ dot }: { dot: HeartbeatDot }) {
  const [delay] = React.useState(() => -dot.age);
  const { kind } = dot.event;
  const style: React.CSSProperties = {
    left: `${dot.x}%`,
    opacity: dot.opacity,
    animationDelay: `${delay}ms`,
  };
  if (kind === "dispatched") {
    style.border = "1.5px solid var(--foreground)";
  } else {
    const color = kind === "run_failed" ? HEALTH_COLOR.failing : HEALTH_COLOR.ok;
    style.background = color;
    style.boxShadow = `0 0 6px ${color}`;
  }
  return (
    <span
      aria-hidden
      className={cn(
        "absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full",
        kind === "dispatched" ? "size-2.5" : kind === "run_failed" ? "size-2" : "size-1.5",
        MOTION_CLASS.phosphor
      )}
      style={style}
    />
  );
});

function Row({
  row,
  selected,
  onSelect,
}: {
  row: HeartbeatRow;
  selected: boolean;
  onSelect?: (agentId: string) => void;
}) {
  const { agent } = row;
  const glow = HEALTH_GLOWS[agent.health] ? rowGlowPercent(agent) : 0;
  const color = HEALTH_COLOR[agent.health];
  const label = `${agent.name}: ${HEALTH_LABEL[agent.health]}, ${Math.round(agent.load * 100)}% load, ${agent.activeRuns} running, ${agent.queued} queued`;
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={label}
      title={label}
      onClick={() => onSelect?.(agent.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(agent.id);
        }
      }}
      className={cn(
        "focus-visible:ring-ring flex h-5 cursor-pointer items-center outline-none focus-visible:ring-2 focus-visible:ring-inset",
        selected && "ring-foreground/60 ring-1 ring-inset"
      )}
      style={
        glow
          ? {
              background: `color-mix(in oklab, ${color} ${glow}%, transparent)`,
              boxShadow: `inset 2px 0 0 ${color}`,
            }
          : undefined
      }
    >
      <div className="flex w-28 shrink-0 items-center gap-1.5 px-2 sm:w-40">
        <span
          aria-hidden
          className={cn(
            "size-1.5 shrink-0 rounded-full",
            agent.health === "failing" && MOTION_CLASS.flicker
          )}
          style={{ background: color }}
        />
        <span
          className={cn(
            "truncate font-mono text-xs",
            agent.health === "idle" ? "text-muted-foreground" : "text-foreground"
          )}
        >
          {agent.name}
        </span>
      </div>
      <div className="relative h-full min-w-0 flex-1 overflow-hidden">
        <div
          className="absolute inset-0"
          style={{ transform: `translateX(calc(var(${DRIFT_VAR}, 0) * -1%))` }}
        >
          {row.dots.map((dot) => (
            <Dot key={dot.event.id} dot={dot} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function FleetHeartbeat({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const rootRef = React.useRef<HTMLDivElement>(null);
  const groups = React.useMemo(() => layoutHeartbeat(snapshot), [snapshot]);
  const hasDots = groups.some((g) => g.rows.some((r) => r.dots.length > 0));

  // Between snapshots, time keeps moving: slide every track left at the
  // window's rate via one CSS variable, reset when a new snapshot places the
  // dots afresh. Nothing to slide, or reduced motion, means no loop.
  React.useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    el.style.setProperty(DRIFT_VAR, "0");
    if (motion === "reduced" || !hasDots) return;
    const start = performance.now();
    let frame = requestAnimationFrame(function tick(t) {
      const drift = Math.min(100, ((t - start) / EVENT_WINDOW_MS) * 100);
      el.style.setProperty(DRIFT_VAR, drift.toFixed(3));
      if (drift < 100) frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [snapshot.now, motion, hasDots]);

  const windowS = Math.round(EVENT_WINDOW_MS / 1000);

  return (
    <div
      ref={rootRef}
      role="group"
      aria-label={summarizeHeartbeat(snapshot)}
      data-motion={motion}
      className={cn("bg-card relative w-full py-2", className)}
    >
      <div className="text-muted-foreground flex h-5 items-center font-mono text-xs">
        <div className="w-28 shrink-0 px-2 sm:w-40">agent</div>
        <div className="relative h-full flex-1">
          <span className="absolute left-1">-{windowS}s</span>
          <span className="absolute left-1/2 -translate-x-1/2">-{windowS / 2}s</span>
          <span className="absolute right-1">now</span>
        </div>
      </div>
      <div className="relative">
        {/* Time gridlines, drawn once behind every track. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 right-0 left-28 sm:left-40"
        >
          {[25, 50, 75].map((p) => (
            <span key={p} className="bg-border absolute inset-y-0 w-px" style={{ left: `${p}%` }} />
          ))}
        </div>
        {groups.map(({ cluster, rows }) => (
          <div key={cluster.id} className="relative">
            <div className="text-muted-foreground flex h-6 items-end gap-1.5 px-2 pb-1 font-mono text-xs tracking-wider uppercase">
              <span
                aria-hidden
                className="size-1.5 shrink-0 rounded-full"
                style={{ background: CLUSTER_COLOR[cluster.health] }}
              />
              <span className="truncate" title={cluster.name}>
                {cluster.name}
              </span>
              {cluster.region && <span className="normal-case opacity-70">{cluster.region}</span>}
              {cluster.health !== "ok" && (
                <span style={{ color: CLUSTER_COLOR[cluster.health] }}>{cluster.health}</span>
              )}
            </div>
            {rows.map((row) => (
              <Row
                key={row.agent.id}
                row={row}
                selected={row.agent.id === selectedAgentId}
                onSelect={onSelectAgent}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
