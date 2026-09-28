"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { iso, isoBox, points, shade } from "../core/iso";
import {
  flowDuration,
  HEALTH_COLOR,
  HEALTH_LABEL,
  MOTION_CLASS,
  type Health,
} from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";
import type { WorkflowLine, WorkflowSnapshot, WorkflowViewProps } from "../core/workflow-model";

import {
  BACKLOG_WARN,
  BELT,
  CRATE_HEALTH,
  MACHINE,
  STEP,
  beltEnd,
  beltStart,
  easeOut,
  isoViewBox,
  laneY,
  placeCrates,
  stationHealth,
  stationWidth,
  summarize,
  truncate,
} from "./workflow-iso-layout";

/**
 * Workflows as isometric assembly lines (spec 44 viz addendum). Each line is
 * a conveyor strip; stages are machines, gates are narrow airlock booths;
 * runs are crates carried along the belt, tweened between snapshots so the
 * movement is the run's real progress. A held crate waits at a shut booth
 * that breathes; a failed crate and its machine turn danger and flicker. The
 * belt's dashes move at the segment's throughput (when particles are on) and
 * a backlog stacks up beside the next machine's intake.
 *
 * Reduced motion: crates are drawn at their snapshot positions with no tween,
 * the belt dashes stand still with opacity carrying throughput, and the
 * booth/backlog/failure colours carry the rest.
 */

export const WORKFLOW_ISOMETRIC_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Run moving (crate) / stage working" },
  { glyph: "signal", color: HEALTH_COLOR.degraded, label: "Held at a gate: booth shut, breathing" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Run failed at that machine" },
  { glyph: "bar", color: HEALTH_COLOR.degraded, label: "Backlog waiting at an intake" },
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Belt speed = throughput" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle / finished" },
];

const UNIT = 20;
const TWEEN_MS = 900;
const CRATE = 0.5;
const BELT_H = 0.12;

function faceColors(health: Health) {
  const top =
    health === "idle"
      ? "color-mix(in oklab, var(--muted-foreground) 16%, var(--card))"
      : `color-mix(in oklab, ${HEALTH_COLOR[health]} 38%, var(--card))`;
  return {
    top,
    left: shade(top, 0.25),
    right: shade(top, 0.45),
    stroke: health === "idle" ? "var(--border)" : HEALTH_COLOR[health],
  };
}

function glow(health: Health): React.CSSProperties | undefined {
  // Glow is for state worth noticing: a failure or a hold, never a busy or idle machine.
  if (health !== "failing" && health !== "degraded") return undefined;
  return {
    filter: `drop-shadow(0 0 5px color-mix(in oklab, ${HEALTH_COLOR[health]} 60%, transparent))`,
  };
}

function onActivate(fn: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

function Box({
  x,
  y,
  w,
  d,
  h,
  z = 0,
  health,
  topClass,
}: {
  x: number;
  y: number;
  w: number;
  d: number;
  h: number;
  z?: number;
  health: Health;
  topClass?: string;
}) {
  const f = isoBox(x, y, w, d, h, UNIT, z);
  const c = faceColors(health);
  return (
    <>
      <polygon points={f.left} fill={c.left} stroke={c.stroke} strokeWidth="0.75" />
      <polygon points={f.right} fill={c.right} stroke={c.stroke} strokeWidth="0.75" />
      <polygon
        points={f.top}
        fill={c.top}
        stroke={c.stroke}
        strokeWidth="0.75"
        className={topClass}
      />
    </>
  );
}

function FocusRing({ x, y, w, d, h }: { x: number; y: number; w: number; d: number; h: number }) {
  const f = isoBox(x, y, w, d, h, UNIT);
  return (
    <polygon
      points={f.top}
      fill="none"
      stroke="var(--ring)"
      strokeWidth="2"
      className="opacity-0 group-focus-visible:opacity-100"
    />
  );
}

function Station({
  snapshot,
  line,
  j,
  y,
  onSelect,
}: {
  snapshot: WorkflowSnapshot;
  line: WorkflowLine;
  j: number;
  y: number;
  onSelect?: (lineId: string, stationId: string) => void;
}) {
  const station = line.stations[j];
  const health = stationHealth(snapshot, line, j);
  const cx = j * STEP;
  const gate = station.kind === "gate";
  const w = stationWidth(line, j);
  const d = gate ? BELT + 0.4 : MACHINE;
  const h = gate ? 1.35 : 1;
  const x0 = cx - w / 2;
  const y0 = y - d / 2;
  const [lx, ly] = iso(cx, y + d / 2 + 0.25, 0, UNIT);
  const select = onSelect ? () => onSelect(line.id, station.id) : undefined;
  const held = gate && health === "degraded";
  const state = gate ? (held ? "holding runs" : "open") : HEALTH_LABEL[health].toLowerCase();

  return (
    <g
      tabIndex={select ? 0 : undefined}
      role={select ? "button" : undefined}
      aria-label={`${line.name}, ${station.name}${gate ? " gate" : ""}: ${state}`}
      onClick={select}
      onKeyDown={select ? onActivate(select) : undefined}
      className={cn("group outline-none", select && "cursor-pointer")}
      style={glow(health)}
    >
      <title>{`${station.name}${gate ? " (gate)" : ""}: ${state}`}</title>
      <Box
        x={x0}
        y={y0}
        w={w}
        d={d}
        h={h}
        health={health}
        topClass={health === "failing" ? MOTION_CLASS.flicker : undefined}
      />
      {gate &&
        (() => {
          // The booth's door on its front face: shut while holding, a gap when open.
          const gap = held ? 0 : 0.28;
          const a = iso(cx - gap / 2, y + d / 2, 0.05, UNIT);
          const b = iso(cx - gap / 2, y + d / 2, h - 0.2, UNIT);
          const c2 = iso(cx + gap / 2, y + d / 2, 0.05, UNIT);
          const d2 = iso(cx + gap / 2, y + d / 2, h - 0.2, UNIT);
          const light = iso(cx, y, h, UNIT);
          return (
            <>
              <polyline
                points={points([a, b])}
                stroke={held ? HEALTH_COLOR.degraded : "var(--border)"}
                strokeWidth="1.25"
              />
              {!held && (
                <polyline points={points([c2, d2])} stroke="var(--border)" strokeWidth="1.25" />
              )}
              <circle
                cx={light[0]}
                cy={light[1]}
                r="2.5"
                fill={held ? HEALTH_COLOR.degraded : HEALTH_COLOR.idle}
                className={held ? MOTION_CLASS.breathe : undefined}
              />
            </>
          );
        })()}
      <FocusRing x={x0 - 0.1} y={y0 - 0.1} w={w + 0.2} d={d + 0.2} h={h} />
      <text
        x={lx}
        y={ly + 12}
        textAnchor="middle"
        fontSize="9"
        className="fill-muted-foreground font-mono"
      >
        {truncate(station.name, 10)}
      </text>
    </g>
  );
}

export function WorkflowIsometric({
  snapshot,
  motion,
  flowParticles = true,
  onSelectRun,
  onSelectStation,
  className,
}: WorkflowViewProps) {
  const vb = React.useMemo(() => isoViewBox(snapshot, UNIT), [snapshot]);
  const placements = React.useMemo(() => placeCrates(snapshot), [snapshot]);
  const crateEls = React.useRef(new Map<string, SVGGElement>());
  const tween = React.useRef(
    new Map<string, { from: number; to: number; y: number; cur: number }>()
  );

  // Crates tween from where they are drawn now to the new snapshot's position,
  // in a rAF loop writing transforms directly; React renders once per snapshot.
  React.useLayoutEffect(() => {
    const state = tween.current;
    const reduced = motion === "reduced";
    for (const id of [...state.keys()]) if (!placements.has(id)) state.delete(id);
    let moving = false;
    for (const [id, p] of placements) {
      const prev = state.get(id);
      // A run that restarted at the top of its line (or a lane change) jumps.
      const jump = !prev || reduced || prev.y !== p.y || p.x < prev.cur - STEP;
      const from = jump ? p.x : prev.cur;
      if (from !== p.x) moving = true;
      state.set(id, { from, to: p.x, y: p.y, cur: from });
    }
    const paint = () => {
      for (const [id, t] of state) {
        const el = crateEls.current.get(id);
        if (!el) continue;
        const [sx, sy] = iso(t.cur, t.y, 0, UNIT);
        el.setAttribute("transform", `translate(${sx.toFixed(1)},${sy.toFixed(1)})`);
      }
    };
    paint();
    if (!moving) return;
    const start = performance.now();
    let raf = 0;
    const frame = (now: number) => {
      const k = easeOut((now - start) / TWEEN_MS);
      for (const t of state.values()) t.cur = t.from + (t.to - t.from) * k;
      paint();
      if (k < 1) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [placements, motion]);

  const segmentsByLine = React.useMemo(() => {
    const m = new Map<string, WorkflowSnapshot["segments"]>();
    for (const s of snapshot.segments) {
      const list = m.get(s.lineId) ?? [];
      list.push(s);
      m.set(s.lineId, list);
    }
    return m;
  }, [snapshot.segments]);

  return (
    <svg
      data-motion={motion}
      role="group"
      aria-label={summarize(snapshot)}
      viewBox={`${vb.x.toFixed(1)} ${vb.y.toFixed(1)} ${vb.w.toFixed(1)} ${vb.h.toFixed(1)}`}
      preserveAspectRatio="xMidYMid meet"
      className={cn("block h-auto w-full", className)}
    >
      {snapshot.lines.map((line, li) => {
        const y = laneY(li);
        const x0 = beltStart();
        const x1 = beltEnd(line);
        const belt = isoBox(x0, y - BELT / 2, x1 - x0, BELT, BELT_H, UNIT);
        const [nx, ny] = iso(x0 - 0.2, y, 0, UNIT);
        const trains = snapshot.trains.filter((t) => t.lineId === line.id);
        const items: { x: number; node: React.ReactNode }[] = [];

        line.stations.forEach((_, j) => {
          items.push({
            x: j * STEP,
            node: (
              <Station
                key={line.stations[j].id}
                snapshot={snapshot}
                line={line}
                j={j}
                y={y}
                onSelect={onSelectStation}
              />
            ),
          });
        });

        for (const t of trains) {
          const p = placements.get(t.id);
          if (!p) continue;
          const health = CRATE_HEALTH[t.state];
          const select = onSelectRun ? () => onSelectRun(t.id) : undefined;
          const status =
            t.state === "held"
              ? `held at ${line.stations[t.at]?.name ?? "gate"}`
              : t.state === "failed"
                ? `failed at ${line.stations[t.at]?.name ?? "stage"}`
                : t.state === "done"
                  ? "finished"
                  : `moving from ${line.stations[t.at]?.name ?? "stage"}`;
          items.push({
            x: p.x,
            node: (
              <g
                key={t.id}
                ref={(el) => {
                  if (el) crateEls.current.set(t.id, el);
                  else crateEls.current.delete(t.id);
                }}
                tabIndex={select ? 0 : undefined}
                role={select ? "button" : undefined}
                aria-label={`Run ${t.label}: ${status}`}
                onClick={select}
                onKeyDown={select ? onActivate(select) : undefined}
                className={cn("group outline-none", select && "cursor-pointer")}
                opacity={t.state === "done" ? 0.45 : 1}
                style={t.state === "failed" ? glow("failing") : undefined}
              >
                <title>{`${t.label}: ${status}`}</title>
                <Box
                  x={-CRATE / 2}
                  y={-CRATE / 2}
                  w={CRATE}
                  d={CRATE}
                  h={0.42}
                  z={BELT_H}
                  health={health}
                  topClass={t.state === "failed" ? MOTION_CLASS.flicker : undefined}
                />
                <FocusRing
                  x={-CRATE / 2 - 0.1}
                  y={-CRATE / 2 - 0.1}
                  w={CRATE + 0.2}
                  d={CRATE + 0.2}
                  h={0.54}
                />
              </g>
            ),
          });
        }
        items.sort((a, b) => a.x - b.x);

        return (
          <g key={line.id}>
            <polygon
              points={belt.left}
              fill={shade("var(--card)", 0.3)}
              stroke="var(--border)"
              strokeWidth="0.75"
            />
            <polygon
              points={belt.right}
              fill={shade("var(--card)", 0.45)}
              stroke="var(--border)"
              strokeWidth="0.75"
            />
            <polygon
              points={belt.top}
              fill="color-mix(in oklab, var(--muted-foreground) 10%, var(--card))"
              stroke="var(--border)"
              strokeWidth="0.75"
            />
            {(segmentsByLine.get(line.id) ?? []).map((s) => {
              if (s.from + 1 >= line.stations.length) return null;
              const a = s.from * STEP + stationWidth(line, s.from) / 2 + 0.1;
              const b = (s.from + 1) * STEP - stationWidth(line, s.from + 1) / 2 - 0.1;
              const backed = s.backlog >= BACKLOG_WARN;
              const flowColor = backed ? HEALTH_COLOR.degraded : HEALTH_COLOR.ok;
              const chips = Math.min(6, s.backlog);
              return (
                <g key={`${s.lineId}-${s.from}`}>
                  {flowParticles && s.rate > 0.02 && (
                    <polyline
                      points={points([iso(a, y, BELT_H, UNIT), iso(b, y, BELT_H, UNIT)])}
                      stroke={flowColor}
                      strokeWidth="1.5"
                      strokeDasharray="2 10"
                      opacity={(0.3 + 0.7 * s.rate).toFixed(2)}
                      className={motion === "full" ? MOTION_CLASS.flow : undefined}
                      style={{ "--viz-flow-duration": flowDuration(s.rate) } as React.CSSProperties}
                    />
                  )}
                  {Array.from({ length: chips }, (_, k) => {
                    // Backlog: runs waiting to enter the next station, stacked beside its intake.
                    const f = isoBox(
                      b - 0.25 - k * 0.3,
                      y + BELT / 2 + 0.1,
                      0.22,
                      0.22,
                      0.18,
                      UNIT
                    );
                    const c = faceColors(backed ? "degraded" : "idle");
                    return (
                      <g key={k} className={backed ? MOTION_CLASS.breathe : undefined}>
                        <polygon points={f.left} fill={c.left} />
                        <polygon points={f.right} fill={c.right} />
                        <polygon points={f.top} fill={c.top} stroke={c.stroke} strokeWidth="0.5" />
                      </g>
                    );
                  })}
                </g>
              );
            })}
            <text
              x={nx}
              y={ny + 3}
              textAnchor="end"
              fontSize="11"
              className="fill-foreground font-mono"
            >
              <title>{line.name}</title>
              {truncate(line.name, 16)}
            </text>
            {items.map((it) => it.node)}
          </g>
        );
      })}
    </svg>
  );
}
