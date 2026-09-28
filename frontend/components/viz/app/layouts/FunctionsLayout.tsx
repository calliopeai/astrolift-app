"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { AppNode, AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, MOTION_CLASS, flowDuration } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import {
  EVENT_WINDOW_MS,
  FLASH_MS,
  edgeFailing,
  functionStats,
  layerNodes,
  recency,
  summarize,
  truncate,
  type FunctionStat,
} from "./layouts-b";
import { tint } from "./layout-parts";
import { fmtRps, healthLabel, selectProps } from "./layouts-b-parts";

/**
 * Functions as an invocation fan-out (spec 44 viz addendum, auto layout for
 * the functions topology). Triggers sit left, functions in columns by call
 * depth, sinks right. Each function tile carries invocations/s, concurrency
 * as a bar, the invocations seen in the last 12s, and a cold-start badge.
 *
 * Motion, and what it means:
 * - An invoked event rings the tile's input port once and lights the tile,
 *   which then fades over 12s; a tile invoked often stays lit.
 * - Edges flow with their rate; red over 5% errors.
 * - "cold" (amber) marks an invocation that came after 5s of quiet.
 * - A failing function flickers.
 *
 * Reduced motion: no ring, fade or flow. The tile's light is a still opacity
 * from the age of its last invocation, and edges are solid with thickness
 * for rate.
 */

export const FUNCTIONS_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "ring", color: HEALTH_COLOR.ok, label: "Invoked just now" },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Tile light: recent invocation, fades over 12s" },
  { glyph: "bar", color: HEALTH_COLOR.ok, label: "Concurrency in use" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, label: "Cold start: invoked after 5s of quiet" },
  {
    glyph: "flow",
    color: HEALTH_COLOR.ok,
    label: "Invocation traffic: faster and thicker is busier",
  },
  { glyph: "flow", color: HEALTH_COLOR.failing, label: "Failing path (over 5% errors)" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing function" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle: stays dark" },
];

const W = 800;
const PAD = 16;
const ROW = 78;
const FN_H = 62;
const SMALL_H = 34;

interface Placed {
  node: AppNode;
  x: number;
  y: number;
  w: number;
  h: number;
}

export type FunctionsLayoutProps = AppViewProps & { diagram: React.ReactNode };

export function FunctionsLayout({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
  diagram,
}: FunctionsLayoutProps) {
  const stats = new Map(functionStats(snapshot).map((s) => [s.node.id, s]));
  const cols = layerNodes(snapshot.nodes, snapshot.edges);
  const colW = W / Math.max(1, cols.length);
  const tileW = Math.min(220, colW - 36);
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const H = rows * ROW + PAD * 2;

  const placed = new Map<string, Placed>();
  cols.forEach((col, ci) => {
    col.forEach((node, ri) => {
      const h = node.role === "function" ? FN_H : SMALL_H;
      const cy = PAD + ((rows - col.length) * ROW) / 2 + ri * ROW + ROW / 2;
      placed.set(node.id, { node, x: ci * colW + (colW - tileW) / 2, y: cy - h / 2, w: tileW, h });
    });
  });

  const maxRps = Math.max(1, ...snapshot.edges.map((e) => e.rps));
  const fns = [...stats.values()];
  const recent = fns.filter((s) => s.last && snapshot.now - s.last.at < EVENT_WINDOW_MS);
  const cold = fns.filter((s) => s.cold).length;
  const label = `${snapshot.app.name}: ${summarize(
    fns.map((s) => s.node),
    "functions"
  )}; ${recent.length} invoked in the last 12s${cold ? `, ${cold} cold starts` : ""}`;

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("flex min-w-0 flex-col gap-3 p-3", className)}
    >
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full">
        {snapshot.edges.map((e) => {
          const a = placed.get(e.from);
          const b = placed.get(e.to);
          if (!a || !b) return null;
          const failing = edgeFailing(e);
          const rate = e.rps / maxRps;
          const idle = e.rps === 0 || a.node.health === "idle";
          const color = failing ? HEALTH_COLOR.failing : idle ? HEALTH_COLOR.idle : HEALTH_COLOR.ok;
          const x1 = a.x + a.w;
          const y1 = a.y + a.h / 2;
          const x2 = b.x;
          const y2 = b.y + b.h / 2;
          const mid = (x1 + x2) / 2;
          const d = `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
          const moving = motion === "full" && flowParticles && !idle;
          return (
            <g key={`${e.from}-${e.to}`}>
              <path d={d} fill="none" stroke={tint(color, 30)} strokeWidth={1 + rate * 5} />
              {moving && (
                <path
                  d={d}
                  fill="none"
                  stroke={color}
                  strokeWidth={2}
                  strokeLinecap="round"
                  className={MOTION_CLASS.flow}
                  style={{ "--viz-flow-duration": flowDuration(rate) } as React.CSSProperties}
                />
              )}
            </g>
          );
        })}
        {[...placed.values()].map((p) =>
          p.node.role === "function" ? (
            <FunctionTile
              key={p.node.id}
              p={p}
              stat={stats.get(p.node.id)}
              now={snapshot.now}
              motion={motion}
              onSelect={onSelectNode}
            />
          ) : (
            <PlainTile key={p.node.id} p={p} onSelect={onSelectNode} />
          )
        )}
      </svg>
      {diagram != null && (
        <div className="h-56 min-w-0 overflow-hidden rounded-sm border">{diagram}</div>
      )}
    </div>
  );
}

function FocusRect({ p }: { p: Placed }) {
  return (
    <rect
      x={p.x - 3}
      y={p.y - 3}
      width={p.w + 6}
      height={p.h + 6}
      rx={3}
      fill="none"
      stroke="var(--ring)"
      strokeWidth={2}
      className="opacity-0 group-focus-visible:opacity-100"
    />
  );
}

/** The light on a tile: lit at invocation, fading over 12s (still under reduced motion). */
const TileLight = React.memo(function TileLight({
  p,
  age,
  color,
}: {
  p: Placed;
  age: number;
  color: string;
}) {
  const [delay] = React.useState(() => -age);
  return (
    <rect
      x={p.x}
      y={p.y}
      width={p.w}
      height={p.h}
      rx={2}
      fill={tint(color, 22)}
      opacity={recency(age, 0)}
      className={MOTION_CLASS.phosphor}
      style={{ animationDelay: `${delay}ms` }}
    />
  );
});

function FunctionTile({
  p,
  stat,
  now,
  motion,
  onSelect,
}: {
  p: Placed;
  stat?: FunctionStat;
  now: number;
  motion: "full" | "reduced";
  onSelect?: (id: string) => void;
}) {
  const { node } = p;
  const color = HEALTH_COLOR[node.health];
  const failing = node.health === "failing";
  const age = stat?.last ? now - stat.last.at : Infinity;
  const pct = Math.round(node.load * 100);
  const barW = p.w - 20;
  return (
    <g
      {...selectProps(node.id, onSelect)}
      aria-label={`${node.name}: ${fmtRps(node.rps)} invocations, concurrency ${pct}%, ${stat?.invocations ?? 0} in the last 12s${stat?.cold ? ", cold start" : ""}, ${healthLabel(node)}`}
      className="group cursor-pointer outline-none"
    >
      <title>{node.name}</title>
      <FocusRect p={p} />
      <rect
        x={p.x}
        y={p.y}
        width={p.w}
        height={p.h}
        rx={2}
        fill="var(--card)"
        stroke={failing ? color : "var(--border)"}
        className={failing ? MOTION_CLASS.flicker : undefined}
      />
      {stat?.last && age < EVENT_WINDOW_MS && (
        <TileLight key={stat.last.id} p={p} age={age} color={color} />
      )}
      {stat?.last && motion === "full" && age <= FLASH_MS && (
        <circle
          key={`r-${stat.last.id}`}
          cx={p.x}
          cy={p.y + p.h / 2}
          r={7}
          fill="none"
          stroke={color}
          strokeWidth={2}
          className={MOTION_CLASS.ripple}
        />
      )}
      <circle cx={p.x + 12} cy={p.y + 15} r={3.5} fill={color} />
      <text x={p.x + 22} y={p.y + 19} fontSize="13" fill="var(--foreground)">
        {truncate(node.name, Math.floor((p.w - 70) / 7.5))}
      </text>
      {stat?.cold && (
        <g>
          <rect
            x={p.x + p.w - 44}
            y={p.y + 6}
            width={36}
            height={16}
            rx={2}
            fill={tint(HEALTH_COLOR.degraded, 18)}
            stroke={HEALTH_COLOR.degraded}
          />
          <text
            x={p.x + p.w - 26}
            y={p.y + 18}
            fontSize="10"
            textAnchor="middle"
            className="font-mono"
            fill={HEALTH_COLOR.degraded}
          >
            cold
          </text>
        </g>
      )}
      <text x={p.x + 10} y={p.y + 38} fontSize="11" className="font-mono" fill="var(--foreground)">
        {fmtRps(node.rps)}
        <tspan fill="var(--muted-foreground)">{`  ${stat?.invocations ?? 0} in 12s`}</tspan>
      </text>
      <rect x={p.x + 10} y={p.y + 47} width={barW} height={5} rx={1} fill="var(--muted)" />
      <rect x={p.x + 10} y={p.y + 47} width={(barW * pct) / 100} height={5} rx={1} fill={color} />
    </g>
  );
}

function PlainTile({ p, onSelect }: { p: Placed; onSelect?: (id: string) => void }) {
  const { node } = p;
  const color = HEALTH_COLOR[node.health];
  return (
    <g
      {...selectProps(node.id, onSelect)}
      aria-label={`${node.name}, ${node.role}, ${healthLabel(node)}`}
      className="group cursor-pointer outline-none"
    >
      <title>{node.name}</title>
      <FocusRect p={p} />
      <rect
        x={p.x}
        y={p.y}
        width={p.w}
        height={p.h}
        rx={2}
        fill="var(--background)"
        stroke="var(--border)"
        strokeDasharray={node.role === "trigger" ? "4 3" : undefined}
      />
      <circle cx={p.x + 12} cy={p.y + p.h / 2} r={3} fill={color} />
      <text x={p.x + 22} y={p.y + p.h / 2 + 4} fontSize="12" fill="var(--foreground)">
        {truncate(node.name, Math.floor((p.w - 60) / 7))}
      </text>
      <text
        x={p.x + p.w - 8}
        y={p.y + p.h / 2 + 4}
        fontSize="10"
        textAnchor="end"
        className="font-mono"
        fill="var(--muted-foreground)"
      >
        {node.kind}
      </text>
    </g>
  );
}
