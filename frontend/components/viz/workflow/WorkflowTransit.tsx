"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { HEALTH_COLOR, MOTION_CLASS, flowDuration } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";
import type { WorkflowSegment, WorkflowTrain, WorkflowViewProps } from "../core/workflow-model";

import {
  layoutTransit,
  particlePeriod,
  positionOnLine,
  trainT,
  truncate,
  type LineGeom,
  type Pt,
} from "./transit-layout";

/**
 * Workflows as a transit map (spec 44 viz addendum). Each workflow is a metro
 * line, stages are stations, gates carry a signal, and runs are trains.
 *
 * Motion, and what it means:
 * - A train glides between snapshots to its real position (at + progress).
 * - A held train waits at its gate's red signal and breathes.
 * - A failed train turns danger, flickers and stops.
 * - A finished train fades out at the terminus.
 * - With flow particles on, dots travel each segment: denser and faster with
 *   throughput, amber where more than three runs are backed up.
 *
 * Reduced motion draws the same state still: trains at their positions,
 * signals lit, no particles, and each segment's backlog as a count badge.
 */

export const WORKFLOW_TRANSIT_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Run moving (a train)" },
  { glyph: "ring", color: HEALTH_COLOR.ok, label: "Run held at a gate, breathing" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Run failed, stopped" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Run done, fades at the terminus" },
  { glyph: "signal", color: HEALTH_COLOR.failing, label: "Gate holding a run" },
  { glyph: "signal", color: HEALTH_COLOR.ok, label: "Gate clear" },
  {
    glyph: "flow",
    color: HEALTH_COLOR.ok,
    label: "Throughput: particle density and speed, line thickness",
  },
  { glyph: "flow", color: HEALTH_COLOR.degraded, label: "Backlog over 3 runs" },
];

/** Tween length; shorter than a snapshot interval so trains settle before the next. */
const TWEEN_MS = 900;
const BACKLOG_WARN = 3;

/** Line identity, not status: tints of the theme accent, so no hue implies health. */
const LINE_TINTS = [100, 62, 80, 45];
function lineColor(i: number): string {
  const pct = LINE_TINTS[i % LINE_TINTS.length];
  return `color-mix(in oklab, var(--brand-primary) ${pct}%, var(--muted-foreground))`;
}

function pathD(poly: Pt[]): string {
  return poly.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join(" ");
}

function trainTransform(line: LineGeom, t: number): string {
  const p = positionOnLine(line, t);
  return `translate(${p.x.toFixed(2)} ${p.y.toFixed(2)}) rotate(${p.angle.toFixed(1)})`;
}

function onActivate(fn: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

const TRAIN_WORD: Record<WorkflowTrain["state"], string> = {
  moving: "moving",
  held: "held",
  failed: "failed",
  done: "done",
};

function trainColor(state: WorkflowTrain["state"]): string {
  if (state === "failed") return HEALTH_COLOR.failing;
  if (state === "done") return HEALTH_COLOR.idle;
  return HEALTH_COLOR.ok;
}

const FOCUS =
  "cursor-pointer outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

export function WorkflowTransit({
  snapshot,
  motion,
  flowParticles = false,
  onSelectRun,
  onSelectStation,
  className,
}: WorkflowViewProps) {
  const layout = React.useMemo(() => layoutTransit(snapshot.lines), [snapshot.lines]);
  const geomById = React.useMemo(() => new Map(layout.lines.map((l) => [l.id, l])), [layout]);
  const lineIndex = React.useMemo(
    () => new Map(snapshot.lines.map((l, i) => [l.id, i])),
    [snapshot.lines]
  );
  const segByKey = React.useMemo(
    () => new Map(snapshot.segments.map((s) => [`${s.lineId}:${s.from}`, s])),
    [snapshot.segments]
  );
  const heldAt = React.useMemo(() => {
    const set = new Set<string>();
    snapshot.trains.forEach((t) => t.state === "held" && set.add(`${t.lineId}:${t.at}`));
    return set;
  }, [snapshot.trains]);

  // Tweening: the displayed position of each train lives in a ref and is
  // written to the DOM from requestAnimationFrame, never through React state.
  const trainEls = React.useRef(new Map<string, SVGGElement>());
  const shown = React.useRef(new Map<string, number>());
  React.useLayoutEffect(() => {
    const plans: { el: SVGGElement; line: LineGeom; id: string; from: number; to: number }[] = [];
    const live = new Set<string>();
    for (const t of snapshot.trains) {
      const line = geomById.get(t.lineId);
      const el = trainEls.current.get(t.id);
      if (!line || !el) continue;
      live.add(t.id);
      const to = trainT(t, line.stations.length);
      const prev = shown.current.get(t.id);
      // A run that re-entered at the first station jumps rather than reversing.
      const from = motion === "reduced" || prev === undefined || prev > to + 0.01 ? to : prev;
      el.setAttribute("transform", trainTransform(line, from));
      shown.current.set(t.id, from);
      if (from !== to) plans.push({ el, line, id: t.id, from, to });
    }
    for (const id of [...shown.current.keys()]) if (!live.has(id)) shown.current.delete(id);
    if (!plans.length) return;
    const start = performance.now();
    let raf = 0;
    const frame = (now: number) => {
      const k = Math.min(1, (now - start) / TWEEN_MS);
      const e = 1 - (1 - k) ** 3;
      for (const p of plans) {
        const t = p.from + (p.to - p.from) * e;
        p.el.setAttribute("transform", trainTransform(p.line, t));
        shown.current.set(p.id, t);
      }
      if (k < 1) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [snapshot.trains, geomById, motion]);

  const counts = { moving: 0, held: 0, failed: 0, done: 0 };
  snapshot.trains.forEach((t) => counts[t.state]++);
  const backedUp = snapshot.segments.filter((s) => s.backlog > BACKLOG_WARN).length;
  const summary =
    `${snapshot.lines.length} workflows, ${snapshot.trains.length} runs: ` +
    `${counts.moving} moving, ${counts.held} held at gates, ${counts.failed} failed, ` +
    `${counts.done} done` +
    (backedUp ? `, ${backedUp} ${backedUp === 1 ? "segment" : "segments"} backed up` : "");

  const reduced = motion === "reduced";
  const particles = flowParticles && !reduced;

  return (
    <div role="group" aria-label={summary} data-motion={motion} className={cn("w-full", className)}>
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        preserveAspectRatio="xMinYMin meet"
        className="block h-auto w-full"
      >
        {snapshot.lines.map((line, i) => {
          const g = geomById.get(line.id)!;
          const color = lineColor(i);
          const segs = g.segments.map((poly, s) => ({
            poly,
            seg: segByKey.get(`${line.id}:${s}`),
          }));
          return (
            <g key={line.id}>
              <text
                x={8}
                y={g.stations[0].y + 4}
                className="fill-foreground font-mono"
                fontSize={11}
                fontWeight={600}
              >
                <title>{line.name}</title>
                {truncate(line.name, 16)}
              </text>
              {segs.map(({ poly, seg }, s) => (
                <path
                  key={s}
                  d={pathD(poly)}
                  fill="none"
                  stroke={color}
                  strokeWidth={5 + (seg?.rate ?? 0) * 5}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ))}
              {particles &&
                segs.map(({ poly, seg }, s) =>
                  seg && seg.rate >= 0.05 ? (
                    <FlowParticles key={`p${s}`} d={pathD(poly)} seg={seg} />
                  ) : null
                )}
              {!particles &&
                segs.map(({ poly, seg }, s) =>
                  seg && seg.backlog > 0 ? (
                    <BacklogBadge key={`b${s}`} poly={poly} backlog={seg.backlog} />
                  ) : null
                )}
              {line.stations.map((st, j) => {
                const p = g.stations[j];
                const held = heldAt.has(`${line.id}:${j}`);
                const terminus = j === line.stations.length - 1;
                const done = terminus
                  ? snapshot.trains.filter((t) => t.lineId === line.id && t.state === "done").length
                  : 0;
                return (
                  <g
                    key={st.id}
                    tabIndex={onSelectStation ? 0 : undefined}
                    role={onSelectStation ? "button" : undefined}
                    aria-label={`${line.name}: ${st.name}${
                      st.kind === "gate" ? (held ? ", gate holding a run" : ", gate clear") : ""
                    }`}
                    className={onSelectStation ? FOCUS : undefined}
                    onClick={onSelectStation && (() => onSelectStation(line.id, st.id))}
                    onKeyDown={onSelectStation && onActivate(() => onSelectStation(line.id, st.id))}
                  >
                    <title>{st.name}</title>
                    {st.kind === "gate" && <Signal at={p} held={held} />}
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={st.kind === "gate" ? 8 : 7}
                      fill="var(--card)"
                      stroke={st.kind === "gate" ? "var(--foreground)" : color}
                      strokeWidth={3}
                    />
                    <text
                      x={p.x}
                      y={p.y + 22}
                      textAnchor="middle"
                      className="fill-muted-foreground font-mono"
                      fontSize={10}
                      style={{ fontVariant: "small-caps", letterSpacing: "0.04em" }}
                    >
                      {truncate(st.name.toLowerCase(), 13)}
                    </text>
                    {done > 0 && (
                      <text
                        x={p.x + 14}
                        y={p.y + 4}
                        className="fill-muted-foreground font-mono"
                        fontSize={10}
                      >
                        {done} done
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
        {snapshot.trains.map((t) => {
          const line = geomById.get(t.lineId);
          if (!line) return null;
          if (t.state === "done" && reduced) return null;
          const name = snapshot.lines[lineIndex.get(t.lineId) ?? 0]?.name ?? t.lineId;
          const station = snapshot.lines[lineIndex.get(t.lineId) ?? 0]?.stations[t.at]?.name ?? "";
          const color = trainColor(t.state);
          const motionClass =
            t.state === "held"
              ? MOTION_CLASS.breathe
              : t.state === "failed"
                ? MOTION_CLASS.flicker
                : undefined;
          return (
            <g
              key={t.id}
              ref={(el) => {
                if (el) trainEls.current.set(t.id, el);
                else trainEls.current.delete(t.id);
              }}
              transform={trainTransform(line, trainT(t, line.stations.length))}
              tabIndex={onSelectRun && t.state !== "done" ? 0 : undefined}
              role={onSelectRun ? "button" : undefined}
              aria-label={`Run ${t.label} on ${name}, ${TRAIN_WORD[t.state]} at ${station}`}
              className={onSelectRun ? FOCUS : undefined}
              style={{
                opacity: t.state === "done" ? 0 : 1,
                transition: t.state === "done" ? "opacity 1.2s ease-out" : undefined,
                pointerEvents: t.state === "done" ? "none" : undefined,
              }}
              onClick={onSelectRun && (() => onSelectRun(t.id))}
              onKeyDown={onSelectRun && onActivate(() => onSelectRun(t.id))}
            >
              <title>{`${t.label} · ${TRAIN_WORD[t.state]}`}</title>
              <rect
                x={-10}
                y={-4.5}
                width={20}
                height={9}
                rx={4.5}
                fill={color}
                stroke="var(--card)"
                strokeWidth={1.5}
                className={motionClass}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** A signal post over a gate: red while a run is held there, green otherwise. */
function Signal({ at, held }: { at: Pt; held: boolean }) {
  const color = held ? HEALTH_COLOR.failing : HEALTH_COLOR.ok;
  return (
    <g aria-hidden>
      <line
        x1={at.x}
        y1={at.y - 8}
        x2={at.x}
        y2={at.y - 16}
        stroke="var(--border)"
        strokeWidth={2}
      />
      <rect
        x={at.x - 6}
        y={at.y - 32}
        width={12}
        height={17}
        rx={2}
        fill="var(--card)"
        stroke="var(--border)"
      />
      {held && (
        <circle
          cx={at.x}
          cy={at.y - 23.5}
          r={8}
          fill={`color-mix(in oklab, ${color} 30%, transparent)`}
        />
      )}
      <circle cx={at.x} cy={at.y - 23.5} r={3.5} fill={color} />
    </g>
  );
}

/** Work moving along a segment: spacing and speed from its rate, amber when backed up. */
function FlowParticles({ d, seg }: { d: string; seg: WorkflowSegment }) {
  const period = particlePeriod(seg.rate);
  const color = seg.backlog > BACKLOG_WARN ? HEALTH_COLOR.degraded : HEALTH_COLOR.ok;
  return (
    <path
      d={d}
      fill="none"
      stroke={color}
      strokeWidth={3}
      strokeLinecap="round"
      className={MOTION_CLASS.flow}
      style={
        {
          "--viz-flow-duration": flowDuration(seg.rate),
          strokeDasharray: `0.01 ${period - 0.01}`,
        } as React.CSSProperties
      }
      aria-hidden
    />
  );
}

/** The still stand-in for particles: how many runs wait to enter the next station. */
function BacklogBadge({ poly, backlog }: { poly: Pt[]; backlog: number }) {
  const a = poly[0];
  const b = poly[poly.length - 1];
  const x = (a.x + b.x) / 2;
  const y = Math.min(a.y, b.y) - 14;
  const warn = backlog > BACKLOG_WARN;
  const color = warn ? HEALTH_COLOR.degraded : "var(--muted-foreground)";
  return (
    <g aria-hidden>
      <title>{`${backlog} waiting`}</title>
      <rect
        x={x - 11}
        y={y - 7}
        width={22}
        height={14}
        rx={2}
        fill={warn ? `color-mix(in oklab, ${color} 18%, var(--card))` : "var(--card)"}
        stroke={color}
      />
      <text x={x} y={y + 3.5} textAnchor="middle" className="font-mono" fontSize={10} fill={color}>
        {backlog}
      </text>
    </g>
  );
}
