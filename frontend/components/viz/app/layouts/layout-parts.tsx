"use client";

import * as React from "react";

import { StatTile } from "@/components/ui/stat-tile";
import { Sparkline } from "@/components/viz/sparkline";
import { cn } from "@/lib/utils";

import type { AppNode } from "../../core/app-model";
import {
  flowDuration,
  HEALTH_COLOR,
  HEALTH_GLOWS,
  HEALTH_LABEL,
  MOTION_CLASS,
  type Health,
} from "../../core/semantics";

import { pushSeries } from "./layout-metrics";

/**
 * Pieces the four auto layouts share. Colour is HEALTH_COLOR only, tints are
 * color-mix of it, and every class that moves comes from MOTION_CLASS, so
 * data-motion="reduced" on the layout root stills all of them.
 */

export const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background";

export function tint(color: string, pct: number): string {
  return `color-mix(in oklab, ${color} ${pct}%, transparent)`;
}

/** A soft halo in the status colour; idle never glows. */
export function glow(health: Health): React.CSSProperties | undefined {
  return HEALTH_GLOWS[health]
    ? { boxShadow: `0 0 8px ${tint(HEALTH_COLOR[health], 70)}` }
    : undefined;
}

/** A status dot. Failing flickers; nothing else moves. */
export function StatusDot({ health, className }: { health: Health; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        health === "failing" && MOTION_CLASS.flicker,
        className
      )}
      style={{ background: HEALTH_COLOR[health], ...glow(health) }}
    />
  );
}

/**
 * Rolling series of live values, one per key, advanced once per snapshot.
 * State is adjusted during render when `now` changes (React's documented
 * pattern for deriving from a changing prop), so nothing runs per frame.
 */
export function useSeries(now: number, values: Record<string, number>): Record<string, number[]> {
  const [state, setState] = React.useState(() => ({
    now,
    series: Object.fromEntries(Object.entries(values).map(([k, v]) => [k, [v]])),
  }));
  if (state.now !== now) {
    const series: Record<string, number[]> = {};
    for (const [k, v] of Object.entries(values)) series[k] = pushSeries(state.series[k] ?? [], v);
    setState({ now, series });
    return series;
  }
  return state.series;
}

/** A KPI: StatTile with a status dot in the label and a sparkline tinted by health. */
export function Kpi({
  label,
  value,
  health,
  series,
  caption,
  className,
}: {
  label: string;
  value: string;
  health: Health;
  series?: number[];
  caption?: React.ReactNode;
  className?: string;
}) {
  return (
    <StatTile
      className={cn("rounded-sm", className)}
      label={
        <span className="inline-flex items-center gap-1.5">
          <StatusDot health={health} />
          <span className="truncate">{label}</span>
          <span className="sr-only">{HEALTH_LABEL[health]}</span>
        </span>
      }
      value={value}
      sparkline={
        series && series.length > 1 ? (
          <span style={{ color: HEALTH_COLOR[health === "idle" ? "idle" : health] }}>
            <Sparkline data={series} width={72} height={22} variant="area" ariaLabel={label} />
          </span>
        ) : undefined
      }
      footer={caption}
    />
  );
}

/**
 * Replica pips: a lit square per ready replica, an outline per missing one.
 * A missing replica is a hold (the rollout is waiting on it), so it breathes.
 */
export function ReplicaPips({
  ready,
  desired,
  health,
}: {
  ready: number;
  desired: number;
  health: Health;
}) {
  const shown = Math.min(desired, 24);
  const color = HEALTH_COLOR[ready < desired && health === "ok" ? "degraded" : health];
  return (
    <span className="inline-flex flex-wrap gap-1" aria-hidden>
      {Array.from({ length: shown }, (_, i) => (
        <span
          key={i}
          className={cn(
            "inline-block size-2.5 rounded-sm border",
            i >= ready && MOTION_CLASS.breathe
          )}
          style={
            i < ready
              ? { background: color, borderColor: color }
              : { borderColor: color, background: "transparent" }
          }
        />
      ))}
    </span>
  );
}

/**
 * Traffic on one link. The dash moves only when calls flow, faster for busier
 * links; with reduced motion or particles off it is a still dash whose weight
 * is the share of traffic, and the number says the rate either way.
 */
export function FlowLine({
  rps,
  share,
  health,
  flowParticles = true,
  vertical = false,
  className,
}: {
  rps: number;
  share: number;
  health: Health;
  flowParticles?: boolean;
  vertical?: boolean;
  className?: string;
}) {
  const color = HEALTH_COLOR[health];
  const moving = rps > 0 && flowParticles;
  const weight = 1 + share * 3;
  const [x2, y2] = vertical ? [0, 100] : [100, 0];
  return (
    <svg
      aria-hidden
      viewBox={vertical ? "-4 0 8 100" : "0 -4 100 8"}
      preserveAspectRatio="none"
      className={cn(vertical ? "h-full w-2" : "h-2 w-full", className)}
    >
      <line
        x1={0}
        y1={0}
        x2={x2}
        y2={y2}
        stroke="var(--border)"
        strokeWidth={weight}
        vectorEffect="non-scaling-stroke"
      />
      {rps > 0 && (
        <line
          x1={0}
          y1={0}
          x2={x2}
          y2={y2}
          stroke={color}
          strokeWidth={weight}
          strokeDasharray={moving ? undefined : "2 6"}
          vectorEffect="non-scaling-stroke"
          className={moving ? MOTION_CLASS.flow : undefined}
          style={moving ? { ["--viz-flow-duration" as string]: flowDuration(share) } : undefined}
        />
      )}
    </svg>
  );
}

/** A selectable node: a focusable button that calls onSelectNode. */
export function NodeButton({
  node,
  health,
  onSelect,
  children,
  className,
  style,
}: {
  node: AppNode;
  health: Health;
  onSelect?: (id: string) => void;
  children?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  const color = HEALTH_COLOR[health];
  return (
    <button
      type="button"
      tabIndex={0}
      title={`${node.name} (${node.kind}): ${HEALTH_LABEL[health]}`}
      onClick={() => onSelect?.(node.id)}
      className={cn(
        "bg-card flex min-w-0 flex-col gap-2 rounded-sm border p-3 text-left",
        FOCUS_RING,
        className
      )}
      style={{
        borderColor: health === "idle" ? "var(--border)" : tint(color, 60),
        background: health === "failing" || health === "degraded" ? tint(color, 8) : undefined,
        ...style,
      }}
    >
      <span className="flex min-w-0 items-center gap-2">
        <StatusDot health={health} />
        <span className="truncate font-mono text-sm font-semibold">{node.name}</span>
        <span className="text-muted-foreground ml-auto shrink-0 font-mono text-xs">
          {node.kind}
        </span>
      </span>
      {children}
    </button>
  );
}

/** A labelled mono figure inside a node card. */
export function Figure({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <span className="flex min-w-0 flex-col">
      <span className="text-muted-foreground truncate text-xs">{label}</span>
      <span className={cn("font-mono tabular-nums", emphasis ? "text-2xl font-bold" : "text-sm")}>
        {value}
      </span>
    </span>
  );
}

/** Where the switcher's diagram (AppIsometric or AppGraph) goes. */
export function DiagramSlot({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  // The classic app style passes no diagram: leave no empty box behind.
  if (children == null) return null;
  return (
    <div className={cn("bg-card relative min-w-0 overflow-hidden rounded-sm border", className)}>
      {children}
    </div>
  );
}
