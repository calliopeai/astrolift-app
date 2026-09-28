"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { HEALTH_COLOR, MOTION_CLASS, flowDuration } from "../core/semantics";
import { WORKFLOW_SHAPE_LEGEND, type LegendItem } from "../core/VizLegend";
import type {
  FanoutBranch,
  FanoutStation,
  SupervisorStation,
  WorkflowLine,
  WorkflowSegment,
  WorkflowStation,
  WorkflowTrain,
  WorkflowViewProps,
} from "../core/workflow-model";
import { joinReady, loopLabel, loopRound, loopsAt, roundLabel } from "../core/workflow-shapes";

import {
  branchSlot,
  layoutTransit,
  lerpPlace,
  particlePeriod,
  placePoint,
  samePlace,
  stationRounds,
  trainPlace,
  truncate,
  tweenStart,
  visibleBranches,
  workerPoint,
  workerSpeed,
  type LineGeom,
  type LoopGeom,
  type Pt,
  type SidingGeom,
  type StationRound,
  type SwarmGeom,
  type TrainPlace,
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
 * - A run sent back rides its loop's return track, arcing above the line
 *   (or round a retry's small loop); the track lights while it does.
 * - At a fanout, each branch is a car on its own siding: it moves to the
 *   join when it settles, so the straggler is the one still lit mid-siding.
 * - A supervisor's busy workers circle it as fast as they are loaded, on
 *   tendrils from the station; idle workers hold still.
 * - With flow particles on, dots travel each segment: denser and faster with
 *   throughput, amber where more than three runs are backed up.
 *
 * Reduced motion draws the same state still: trains at their positions (on
 * the return arc when sent back), cars in their slots, workers at rest, no
 * particles, and each segment's backlog as a count badge.
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
  {
    ...WORKFLOW_SHAPE_LEGEND.returnTrack,
    label:
      "Return track over the line: a loop back, with its trigger and bound; lit while a run rides it",
  },
  {
    ...WORKFLOW_SHAPE_LEGEND.returnTrack,
    label: "Small loop over one station: a retry, with its attempt bound",
  },
  { ...WORKFLOW_SHAPE_LEGEND.roundBadge, label: "Round badge: round a run is on, of the bound" },
  WORKFLOW_SHAPE_LEGEND.roundNearBound,
  {
    glyph: "siding",
    color: HEALTH_COLOR.ok,
    label: "Siding: one parallel branch; its car lit while running, at the join once settled",
  },
  { glyph: "dot", color: HEALTH_COLOR.failing, label: "Branch failed (settled, in red)" },
  { ...WORKFLOW_SHAPE_LEGEND.join, label: "Join: glows once every branch has arrived" },
  {
    ...WORKFLOW_SHAPE_LEGEND.supervisorSwarm,
    label: "Supervisor swarm: busy workers lit on tendrils, circling as fast as they are loaded",
  },
  {
    ...WORKFLOW_SHAPE_LEGEND.nestedStack,
    label: "Stacked station: a child workflow; open it to see its line",
  },
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

const mix = (c: string, pct: number) => `color-mix(in oklab, ${c} ${pct}%, transparent)`;

function pathD(poly: Pt[]): string {
  return poly.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join(" ");
}

function placeTransform(line: LineGeom, place: TrainPlace): string {
  const p = placePoint(line, place);
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

function branchColor(b: FanoutBranch): string {
  if (b.state === "failed") return HEALTH_COLOR.failing;
  if (b.state === "queued") return HEALTH_COLOR.idle;
  return HEALTH_COLOR.ok;
}

/** Every station and run takes focus; only the ones with an action look clickable. */
const FOCUS_RING =
  "outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";
const FOCUS = `cursor-pointer ${FOCUS_RING}`;

/** A run's round and loop state in words, for its accessible name. */
function trainShapeWords(t: WorkflowTrain, line: WorkflowLine): string {
  const parts: string[] = [];
  if (t.onLoopId) {
    const loop = line.loops?.find((l) => l.id === t.onLoopId);
    if (loop) {
      const from = line.stations[loop.from]?.name ?? "stage";
      const to = line.stations[loop.to]?.name ?? "stage";
      const next = loopRound(t, loop) + 1;
      parts.push(
        loop.kind === "retry"
          ? `retrying ${from}, attempt ${next} of ${loop.maxRounds} next`
          : `sent back to ${to} because ${from} ${loop.trigger === "rejected" ? "was rejected" : loop.trigger === "failed" ? "failed" : "met its condition"}, round ${next} of ${loop.maxRounds} next`
      );
    }
  } else {
    for (const loop of loopsAt(line, t.at)) {
      if (loopRound(t, loop) > 1) parts.push(roundLabel(t, loop));
    }
  }
  if ((t.round ?? 1) > 1) parts.unshift(`round ${t.round} overall`);
  if (t.subtasks) parts.push(`${t.subtasks.done} of ${t.subtasks.total} sub-tasks done`);
  return parts.length ? `, ${parts.join(", ")}` : "";
}

export interface WorkflowTransitProps extends WorkflowViewProps {
  /** Nested stations (by station id) that start open, their child lines drawn in place. */
  defaultExpanded?: string[];
}

export function WorkflowTransit({
  snapshot,
  motion,
  flowParticles = false,
  onSelectRun,
  onSelectStation,
  className,
  defaultExpanded,
}: WorkflowTransitProps) {
  const [expanded, setExpanded] = React.useState<ReadonlySet<string>>(
    () => new Set(defaultExpanded ?? [])
  );
  const layout = React.useMemo(
    () => layoutTransit(snapshot.lines, expanded),
    [snapshot.lines, expanded]
  );
  const geomById = React.useMemo(() => new Map(layout.lines.map((l) => [l.id, l])), [layout]);
  const lineById = React.useMemo(
    () => new Map(snapshot.lines.map((l) => [l.id, l])),
    [snapshot.lines]
  );
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
  const trainById = React.useMemo(
    () => new Map(snapshot.trains.map((t) => [t.id, t])),
    [snapshot.trains]
  );

  const reduced = motion === "reduced";
  const particles = flowParticles && !reduced;

  // One requestAnimationFrame loop, started when something needs to move and
  // stopped when nothing does. It writes train and worker positions straight
  // to the DOM through refs, never through React state.
  const trainEls = React.useRef(new Map<string, SVGGElement>());
  const shown = React.useRef(new Map<string, TrainPlace>());
  const workerEls = React.useRef(new Map<string, SVGGElement>());
  const tendrilEls = React.useRef(new Map<string, SVGPathElement>());
  const angles = React.useRef(new Map<string, number>());
  const stationEls = React.useRef(new Map<string, SVGGElement>());
  const anim = React.useRef({
    plans: [] as { id: string; line: LineGeom; from: TrainPlace; to: TrainPlace }[],
    start: 0,
    workers: [] as { id: string; swarm: SwarmGeom; station: Pt; speed: number }[],
  });
  const kick = React.useRef<() => void>(() => {});
  const registerWorker = React.useCallback((id: string, el: SVGGElement | null) => {
    if (el) workerEls.current.set(id, el);
    else workerEls.current.delete(id);
  }, []);
  const registerTendril = React.useCallback((id: string, el: SVGPathElement | null) => {
    if (el) tendrilEls.current.set(id, el);
    else tendrilEls.current.delete(id);
  }, []);

  React.useLayoutEffect(() => {
    const plans: (typeof anim.current)["plans"] = [];
    const live = new Set<string>();
    for (const t of snapshot.trains) {
      const geom = geomById.get(t.lineId);
      const line = lineById.get(t.lineId);
      const el = trainEls.current.get(t.id);
      if (!geom || !line || !el) continue;
      live.add(t.id);
      const to = trainPlace(t, line, geom);
      const from = reduced ? to : tweenStart(shown.current.get(t.id), to, geom);
      el.setAttribute("transform", placeTransform(geom, from));
      shown.current.set(t.id, from);
      if (!samePlace(from, to)) plans.push({ id: t.id, line: geom, from, to });
    }
    for (const id of [...shown.current.keys()]) if (!live.has(id)) shown.current.delete(id);

    const workers: (typeof anim.current)["workers"] = [];
    for (const geom of layout.lines) {
      const line = lineById.get(geom.id);
      for (const swarm of geom.swarms) {
        const st = line?.stations[swarm.station] as SupervisorStation | undefined;
        swarm.workers.forEach((seed, k) => {
          const w = st?.workers[k];
          if (!angles.current.has(seed.id)) angles.current.set(seed.id, seed.phase);
          const a = reduced ? seed.phase : angles.current.get(seed.id)!;
          const station = geom.stations[swarm.station];
          writeWorker(workerEls.current, tendrilEls.current, seed.id, swarm, station, a);
          if (w?.busy)
            workers.push({ id: seed.id, swarm, station, speed: workerSpeed(true, w.load) });
        });
      }
    }
    anim.current = { plans, start: performance.now(), workers };
    kick.current();
  }, [snapshot.trains, layout, geomById, lineById, reduced]);

  React.useEffect(() => {
    if (reduced || typeof window.requestAnimationFrame !== "function") return;
    let raf = 0;
    let last = 0;
    const frame = (now: number) => {
      raf = 0;
      const a = anim.current;
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 0;
      last = now;
      let more = false;
      if (a.plans.length) {
        const k = Math.min(1, Math.max(0, (now - a.start) / TWEEN_MS));
        const e = 1 - (1 - k) ** 3;
        for (const p of a.plans) {
          const place = lerpPlace(p.from, p.to, e);
          trainEls.current.get(p.id)?.setAttribute("transform", placeTransform(p.line, place));
          shown.current.set(p.id, place);
        }
        if (k < 1) more = true;
        else a.plans = [];
      }
      for (const w of a.workers) {
        const angle = (angles.current.get(w.id) ?? 0) + w.speed * dt;
        angles.current.set(w.id, angle);
        writeWorker(workerEls.current, tendrilEls.current, w.id, w.swarm, w.station, angle);
        more = true;
      }
      if (more) raf = window.requestAnimationFrame(frame);
      else last = 0;
    };
    kick.current = () => {
      if (!raf) raf = window.requestAnimationFrame(frame);
    };
    kick.current();
    return () => {
      window.cancelAnimationFrame(raf);
      kick.current = () => {};
    };
  }, [reduced]);

  const toggle = (stationId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(stationId)) next.delete(stationId);
      else next.add(stationId);
      return next;
    });

  // The way back from a child line: close it and return focus to its stacked station.
  const closeChild = (stationId: string) => {
    toggle(stationId);
    stationEls.current.get(stationId)?.focus();
  };

  const counts = { moving: 0, held: 0, failed: 0, done: 0 };
  snapshot.trains.forEach((t) => counts[t.state]++);
  const sentBack = snapshot.trains.filter((t) => t.onLoopId).length;
  const backedUp = snapshot.segments.filter((s) => s.backlog > BACKLOG_WARN).length;
  const topLevel = snapshot.lines.filter((l) => !l.parent).length;
  const summary =
    `${topLevel} ${topLevel === 1 ? "workflow" : "workflows"}, ${snapshot.trains.length} ${snapshot.trains.length === 1 ? "run" : "runs"}: ` +
    `${counts.moving} moving, ${counts.held} held, ${counts.failed} failed, ` +
    `${counts.done} done` +
    (sentBack ? `, ${sentBack} sent back on a loop` : "") +
    (backedUp ? `, ${backedUp} ${backedUp === 1 ? "segment" : "segments"} backed up` : "");

  return (
    <div role="group" aria-label={summary} data-motion={motion} className={cn("w-full", className)}>
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        preserveAspectRatio="xMinYMin meet"
        className="block h-auto w-full"
      >
        {layout.lines.map((g) => {
          const line = lineById.get(g.id)!;
          const color = lineColor(lineIndex.get(line.id) ?? 0);
          const rounds = stationRounds(line, snapshot.trains);
          const segs = g.segments.map((poly, s) => ({
            poly,
            s,
            seg: segByKey.get(`${line.id}:${s}`),
            siding: g.sidingSegments.includes(s),
          }));
          const lineTrains = snapshot.trains.filter((t) => t.lineId === line.id);
          const parentLine = g.parent && lineById.get(g.parent.lineId);
          const parentGeom = g.parent && geomById.get(g.parent.lineId);
          const nestedId = parentLine?.stations[g.parent!.station]?.id;
          return (
            <g key={line.id}>
              {parentGeom && (
                <path
                  d={`M${parentGeom.stations[g.parent!.station].x} ${parentGeom.stations[g.parent!.station].y + 9} L${g.stations[0].x} ${g.stations[0].y - 9}`}
                  stroke={color}
                  strokeWidth={2}
                  strokeDasharray="3 3"
                  aria-hidden
                />
              )}
              <text
                x={g.labelAt.x}
                y={g.labelAt.y}
                className="fill-foreground font-mono"
                fontSize={11}
                fontWeight={600}
              >
                <title>{line.name}</title>
                {g.depth ? "↳ " : ""}
                {truncate(line.name, 16 - g.depth * 2)}
              </text>
              {parentLine && nestedId && (
                <BackLink
                  at={{ x: g.labelAt.x, y: g.labelAt.y + 16 }}
                  parent={parentLine.name}
                  child={line.name}
                  onBack={() => closeChild(nestedId)}
                />
              )}
              {segs.map(({ poly, s, seg, siding }) =>
                siding ? null : (
                  <path
                    key={s}
                    d={pathD(poly)}
                    fill="none"
                    stroke={color}
                    strokeWidth={5 + (seg?.rate ?? 0) * 5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                )
              )}
              {g.sidings.map((sd) => (
                <Sidings
                  key={`sd${sd.fanout}`}
                  sidings={sd}
                  fanout={line.stations[sd.fanout] as FanoutStation}
                  color={color}
                  reduced={reduced}
                />
              ))}
              {g.loops.map((lg) => (
                <ReturnTrack
                  key={lg.id}
                  geom={lg}
                  full={loopLabel(line, line.loops!.find((l) => l.id === lg.id)!)}
                  active={lineTrains.some((t) => t.onLoopId === lg.id)}
                />
              ))}
              {particles &&
                segs.map(({ poly, s, seg, siding }) =>
                  !siding && seg && seg.rate >= 0.05 ? (
                    <FlowParticles key={`p${s}`} d={pathD(poly)} seg={seg} />
                  ) : null
                )}
              {!particles &&
                segs.map(({ poly, s, seg, siding }) =>
                  !siding && seg && seg.backlog > 0 ? (
                    <BacklogBadge key={`b${s}`} poly={poly} backlog={seg.backlog} />
                  ) : null
                )}
              {g.swarms.map((sw) => (
                <Swarm
                  key={`sw${sw.station}`}
                  swarm={sw}
                  station={g.stations[sw.station]}
                  supervisor={line.stations[sw.station] as SupervisorStation}
                  registerWorker={registerWorker}
                  registerTendril={registerTendril}
                />
              ))}
              {line.stations.map((st, j) => {
                const p = g.stations[j];
                const held = heldAt.has(`${line.id}:${j}`);
                const terminus = j === line.stations.length - 1;
                const done = terminus ? lineTrains.filter((t) => t.state === "done").length : 0;
                const nested = st.kind === "workflow";
                const open = nested && expanded.has(st.id) && lineById.has(st.childLineId);
                const selectable = !!onSelectStation || nested;
                const activate = () => {
                  if (nested) toggle(st.id);
                  onSelectStation?.(line.id, st.id);
                };
                const label = stationLabel({
                  line,
                  station: st,
                  index: j,
                  held,
                  round: rounds.get(j),
                  trains: lineTrains,
                  trainById,
                  lineById,
                  open,
                });
                return (
                  <g
                    key={st.id}
                    ref={(el) => {
                      if (el) stationEls.current.set(st.id, el);
                      else stationEls.current.delete(st.id);
                    }}
                    tabIndex={0}
                    role={selectable ? "button" : "img"}
                    aria-label={label}
                    aria-expanded={nested ? open : undefined}
                    className={selectable ? FOCUS : FOCUS_RING}
                    onClick={selectable ? activate : undefined}
                    onKeyDown={selectable ? onActivate(activate) : undefined}
                  >
                    <title>{label}</title>
                    {st.kind === "gate" && <Signal at={p} held={held} />}
                    <StationMark station={st} at={p} color={color} open={open} line={line} />
                    <text
                      x={p.x}
                      y={p.y + (st.kind === "fanout" || st.kind === "join" ? 44 : 22)}
                      textAnchor="middle"
                      className="fill-muted-foreground font-mono"
                      fontSize={10}
                      style={{ fontVariant: "small-caps", letterSpacing: "0.04em" }}
                    >
                      {truncate(st.name.toLowerCase(), 13)}
                    </text>
                    {rounds.get(j) && <RoundBadge at={p} round={rounds.get(j)!} />}
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
          const geom = geomById.get(t.lineId);
          const line = lineById.get(t.lineId);
          if (!geom || !line) return null;
          if (t.state === "done" && reduced) return null;
          const station = line.stations[t.at]?.name ?? "";
          const color = trainColor(t.state);
          const motionClass =
            t.state === "held"
              ? MOTION_CLASS.breathe
              : t.state === "failed"
                ? MOTION_CLASS.flicker
                : undefined;
          const label = `Run ${t.label} on ${line.name}, ${TRAIN_WORD[t.state]} at ${station}${trainShapeWords(t, line)}`;
          return (
            <g
              key={t.id}
              ref={(el) => {
                if (el) trainEls.current.set(t.id, el);
                else trainEls.current.delete(t.id);
              }}
              transform={placeTransform(geom, trainPlace(t, line, geom))}
              tabIndex={t.state !== "done" ? 0 : undefined}
              role={onSelectRun ? "button" : "img"}
              aria-label={label}
              className={onSelectRun ? FOCUS : FOCUS_RING}
              style={{
                opacity: t.state === "done" ? 0 : 1,
                transition: t.state === "done" ? "opacity 1.2s ease-out" : undefined,
                pointerEvents: t.state === "done" ? "none" : undefined,
              }}
              onClick={onSelectRun && (() => onSelectRun(t.id))}
              onKeyDown={onSelectRun && onActivate(() => onSelectRun(t.id))}
            >
              <title>{label}</title>
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

function writeWorker(
  workerEls: Map<string, SVGGElement>,
  tendrilEls: Map<string, SVGPathElement>,
  id: string,
  swarm: SwarmGeom,
  station: Pt,
  angle: number
) {
  const p = workerPoint(swarm, angle);
  workerEls.get(id)?.setAttribute("transform", `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`);
  tendrilEls.get(id)?.setAttribute("d", tendril(station, p));
}

function tendril(from: Pt, to: Pt): string {
  const s = { x: from.x, y: from.y - 8 };
  const c = { x: (s.x + to.x) / 2 + (to.y - s.y) * 0.25, y: (s.y + to.y) / 2 };
  return `M${s.x.toFixed(1)} ${s.y.toFixed(1)} Q${c.x.toFixed(1)} ${c.y.toFixed(1)} ${to.x.toFixed(1)} ${to.y.toFixed(1)}`;
}

/** The way back from an opened child line to its parent. */
function BackLink({
  at,
  parent,
  child,
  onBack,
}: {
  at: Pt;
  parent: string;
  child: string;
  onBack: () => void;
}) {
  return (
    <text
      x={at.x}
      y={at.y}
      role="button"
      tabIndex={0}
      aria-label={`Back to ${parent}: close ${child}`}
      className={cn("fill-muted-foreground font-mono underline", FOCUS)}
      fontSize={10}
      onClick={onBack}
      onKeyDown={onActivate(onBack)}
    >
      <title>{`Back to ${parent}`}</title>
      {truncate(`back to ${parent}`, 20)}
    </text>
  );
}

/** A station's accessible name: what it is and its state, in words. */
function stationLabel({
  line,
  station: st,
  index,
  held,
  round,
  trains,
  trainById,
  lineById,
  open,
}: {
  line: WorkflowLine;
  station: WorkflowStation;
  index: number;
  held: boolean;
  round?: StationRound;
  trains: WorkflowTrain[];
  trainById: Map<string, WorkflowTrain>;
  lineById: Map<string, WorkflowLine>;
  open: boolean;
}): string {
  const parts = [`${line.name}: ${st.name}`];
  if (round)
    parts.push(
      `${round.loop.kind === "retry" ? "attempt" : "round"} ${round.round} of ${round.loop.maxRounds}${round.near ? ", near the bound" : ""}`
    );
  const here = trains.filter((t) => t.at === index && !t.onLoopId);
  if (here.some((t) => t.state === "failed")) parts.push("failed");
  if (st.kind === "gate") parts.push(held ? "gate holding a run" : "gate clear");
  if (st.kind === "fanout") {
    const r = joinReady(st);
    if (!st.runId)
      parts.push(
        st.dynamic ? "parallel branches, count set per run, idle" : "parallel branches, idle"
      );
    else {
      const straggler = st.branches.filter((b) => b.state === "running" || b.state === "queued");
      parts.push(
        `${r.settled} of ${r.total} branches settled${r.failed ? `, ${r.failed} failed` : ""}` +
          (straggler.length && straggler.length <= 2
            ? `, waiting on ${straggler.map((b) => b.label).join(" and ")}`
            : "")
      );
    }
  }
  if (st.kind === "join") {
    const fan = line.stations[st.waitsOn];
    if (fan?.kind === "fanout" && fan.runId) {
      const r = joinReady(fan);
      parts.push(r.ready ? "join ready" : `join waiting on ${r.pending} of ${r.total} branches`);
    } else if (here.some((t) => t.state === "moving")) parts.push("merging");
  }
  if (st.kind === "supervisor") {
    const busy = st.workers.filter((w) => w.busy).length;
    parts.push(`supervisor, ${busy} of ${st.workers.length} workers busy`);
    const sub = here.find((t) => t.subtasks);
    if (sub?.subtasks) parts.push(`${sub.subtasks.done} of ${sub.subtasks.total} sub-tasks done`);
  }
  if (st.kind === "workflow") {
    const child = lineById.get(st.childLineId);
    parts.push(`child workflow ${child?.name ?? ""}`.trim());
    const run = here.map((t) => t.childRunId && trainById.get(t.childRunId)).find(Boolean);
    if (run && child) parts.push(`child run at ${child.stations[run.at]?.name ?? "start"}`);
    parts.push(open ? "open" : "closed");
  }
  for (const loop of loopsAt(line, index)) parts.push(loopLabel(line, loop));
  return parts.join(", ");
}

/** The station glyph: a circle, a gate ring, a join block, or a stack for a child workflow. */
function StationMark({
  station: st,
  at: p,
  color,
  open,
  line,
}: {
  station: WorkflowStation;
  at: Pt;
  color: string;
  open: boolean;
  line: WorkflowLine;
}) {
  if (st.kind === "workflow") {
    return (
      <g>
        <rect
          x={p.x - 6}
          y={p.y - 11}
          width={16}
          height={12}
          rx={2}
          fill="var(--card)"
          stroke="var(--border)"
          strokeWidth={1.5}
        />
        <rect
          x={p.x - 8}
          y={p.y - 7}
          width={16}
          height={12}
          rx={2}
          fill="var(--card)"
          stroke="var(--border)"
          strokeWidth={1.5}
        />
        <rect
          x={p.x - 10}
          y={p.y - 3}
          width={16}
          height={12}
          rx={2}
          fill={open ? color : "var(--card)"}
          stroke={color}
          strokeWidth={2.5}
        />
      </g>
    );
  }
  if (st.kind === "join") {
    const fan = line.stations[st.waitsOn];
    const ready = fan?.kind === "fanout" && !!fan.runId && joinReady(fan).ready;
    return (
      <rect
        x={p.x - 8}
        y={p.y - 8}
        width={16}
        height={16}
        rx={3}
        fill={ready ? HEALTH_COLOR.ok : "var(--card)"}
        stroke={ready ? HEALTH_COLOR.ok : color}
        strokeWidth={3}
        style={ready ? { filter: `drop-shadow(0 0 6px ${mix(HEALTH_COLOR.ok, 70)})` } : undefined}
      />
    );
  }
  return (
    <circle
      cx={p.x}
      cy={p.y}
      r={st.kind === "gate" ? 8 : 7}
      fill="var(--card)"
      stroke={st.kind === "gate" ? "var(--foreground)" : color}
      strokeWidth={3}
    />
  );
}

/** "3 / 5" over a station a loop leaves from; amber once one round from the bound. */
function RoundBadge({ at, round }: { at: Pt; round: StationRound }) {
  const color = round.near ? HEALTH_COLOR.degraded : HEALTH_COLOR.ok;
  const text = `${round.round} / ${round.loop.maxRounds}`;
  const w = 8 + text.length * 6;
  return (
    <g aria-hidden>
      <rect
        x={at.x + 10}
        y={at.y - 24}
        width={w}
        height={13}
        rx={6.5}
        fill={round.near ? `color-mix(in oklab, ${color} 18%, var(--card))` : "var(--card)"}
        stroke={color}
      />
      <text
        x={at.x + 10 + w / 2}
        y={at.y - 14.5}
        textAnchor="middle"
        className="font-mono"
        fontSize={9.5}
        fill={color}
      >
        {text}
      </text>
    </g>
  );
}

/** A loop's way back: an arc above the line, arrowed at its target, labelled with trigger and bound. */
function ReturnTrack({ geom, full, active }: { geom: LoopGeom; full: string; active: boolean }) {
  const color = HEALTH_COLOR.degraded;
  const end = geom.poly[geom.poly.length - 1];
  const prev = geom.poly[geom.poly.length - 3];
  const a = Math.atan2(end.y - prev.y, end.x - prev.x);
  const head = (da: number) =>
    `${(end.x - Math.cos(a + da) * 6).toFixed(1)} ${(end.y - Math.sin(a + da) * 6).toFixed(1)}`;
  const w = geom.label.length * 5.4 + 8;
  const lx = geom.anchor === "end" ? geom.labelAt.x - w + 4 : geom.labelAt.x - w / 2;
  return (
    <g aria-hidden>
      <title>{full}</title>
      <path
        d={geom.d}
        fill="none"
        stroke={active ? color : mix(color, 55)}
        strokeWidth={active ? 3 : 2}
        strokeDasharray={active ? undefined : "4 3"}
        strokeLinecap="round"
      />
      <path
        d={`M${head(0.5)} L${end.x.toFixed(1)} ${end.y.toFixed(1)} L${head(-0.5)}`}
        fill="none"
        stroke={active ? color : mix(color, 55)}
        strokeWidth={2}
      />
      <rect x={lx} y={geom.labelAt.y - 9} width={w} height={12} rx={2} fill="var(--card)" />
      <text
        x={geom.labelAt.x}
        y={geom.labelAt.y}
        textAnchor={geom.anchor}
        className="font-mono"
        fontSize={9}
        fill={active ? color : "var(--muted-foreground)"}
      >
        {geom.label}
      </text>
    </g>
  );
}

/**
 * A fanout's sidings. With a run holding it, each branch is a car on its
 * track: at the split while queued, lit mid-siding while running, at the
 * join once settled (red if it failed). A car is re-created, not slid back,
 * when a new run's branches start queued.
 */
function Sidings({
  sidings: sd,
  fanout,
  color,
  reduced,
}: {
  sidings: SidingGeom;
  fanout: FanoutStation;
  color: string;
  reduced: boolean;
}) {
  const { shown, hidden } = visibleBranches(fanout.branches);
  const holding = !!fanout.runId;
  const hiddenPending = hidden.filter((b) => b.state === "queued" || b.state === "running").length;
  return (
    <g aria-hidden>
      {sd.tracks.map((tr, k) => (
        <path
          key={k}
          d={tr.d}
          fill="none"
          stroke={color}
          strokeWidth={2.5}
          strokeLinejoin="round"
          strokeDasharray={sd.placeholder ? "3 4" : undefined}
          opacity={holding ? 1 : 0.6}
        />
      ))}
      {holding &&
        shown.map((b, k) => {
          const tr = sd.tracks[k];
          if (!tr) return null;
          const p = tr.slots[branchSlot(b.state)];
          const c = branchColor(b);
          const lit = b.state === "running";
          return (
            <g
              key={b.state === "queued" ? `${b.id}:q` : b.id}
              style={{
                transform: `translate(${p.x}px, ${p.y}px)`,
                transition: reduced ? undefined : "transform 700ms var(--ease-standard)",
              }}
            >
              <title>{`${b.label}: ${b.state}`}</title>
              <circle
                r={lit ? 3.5 : 2.75}
                fill={c}
                opacity={b.state === "succeeded" ? 0.6 : 1}
                style={lit ? { filter: `drop-shadow(0 0 4px ${mix(c, 80)})` } : undefined}
              />
            </g>
          );
        })}
      {sd.hidden > 0 && (
        <text
          x={sd.moreAt.x}
          y={sd.moreAt.y}
          textAnchor="middle"
          className="font-mono"
          fontSize={9}
          fill={hiddenPending ? HEALTH_COLOR.ok : "var(--muted-foreground)"}
        >
          {`+${sd.hidden}${hiddenPending ? `, ${hiddenPending} running` : ""}`}
        </text>
      )}
      {holding && (
        <text
          x={sd.moreAt.x}
          y={sd.tracks[sd.tracks.length - 1].slots.running.y + 14}
          textAnchor="middle"
          className="fill-muted-foreground font-mono"
          fontSize={9}
        >
          {`${joinReady(fanout).settled}/${fanout.branches.length}`}
        </text>
      )}
    </g>
  );
}

/** A supervisor's workers around a small hive above it, tendrils to the busy ones. */
function Swarm({
  swarm,
  station,
  supervisor,
  registerWorker,
  registerTendril,
}: {
  swarm: SwarmGeom;
  station: Pt;
  supervisor: SupervisorStation;
  registerWorker: (id: string, el: SVGGElement | null) => void;
  registerTendril: (id: string, el: SVGPathElement | null) => void;
}) {
  const busy = supervisor.workers.filter((w) => w.busy).length;
  return (
    <g aria-hidden>
      <circle
        cx={swarm.hive.x}
        cy={swarm.hive.y}
        r={swarm.r + 6}
        fill={mix("var(--brand-primary)", 10)}
      />
      <line
        x1={station.x}
        y1={station.y - 8}
        x2={swarm.hive.x}
        y2={swarm.hive.y}
        stroke="var(--border)"
        strokeWidth={1.5}
      />
      {swarm.workers.map((seed, k) => {
        const w = supervisor.workers[k];
        if (!w?.busy) return null;
        const p = workerPoint(swarm, seed.phase);
        return (
          <path
            key={`t${seed.id}`}
            ref={(el) => registerTendril(seed.id, el)}
            d={tendril(station, p)}
            fill="none"
            stroke={mix(HEALTH_COLOR.ok, 60)}
            strokeWidth={1 + w.load * 1.5}
            strokeLinecap="round"
          />
        );
      })}
      {swarm.workers.map((seed, k) => {
        const w = supervisor.workers[k];
        if (!w) return null;
        const p = workerPoint(swarm, seed.phase);
        const color = w.busy ? HEALTH_COLOR.ok : HEALTH_COLOR.idle;
        return (
          <g
            key={seed.id}
            ref={(el) => registerWorker(seed.id, el)}
            transform={`translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`}
          >
            <title>{`${w.label}: ${w.busy ? "busy" : "idle"}, ${Math.round(w.load * 100)}% load`}</title>
            <circle
              r={2 + w.load * 2}
              fill={color}
              opacity={w.busy ? 1 : 0.55}
              style={w.busy ? { filter: `drop-shadow(0 0 3px ${mix(color, 70)})` } : undefined}
            />
          </g>
        );
      })}
      <text
        x={swarm.hive.x + swarm.r + 10}
        y={swarm.hive.y + 3}
        className="fill-muted-foreground font-mono"
        fontSize={9}
      >
        {`${busy}/${supervisor.workers.length}`}
      </text>
    </g>
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
