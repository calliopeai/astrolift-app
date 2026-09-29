"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { iso, isoBox, points, shade, type Point } from "../core/iso";
import { flowDuration, HEALTH_COLOR, MOTION_CLASS, type Health } from "../core/semantics";
import { WORKFLOW_SHAPE_LEGEND, type LegendItem } from "../core/VizLegend";
import type {
  FanoutStation,
  NestedStation,
  SupervisorStation,
  WorkflowLine,
  WorkflowSnapshot,
  WorkflowTrain,
  WorkflowViewProps,
} from "../core/workflow-model";
import { isNearBound, joinReady, loopLabel } from "../core/workflow-shapes";

import {
  BACKLOG_WARN,
  BELT,
  BELT_H,
  CRATE_HEALTH,
  DECK,
  LEVEL,
  MACHINE,
  PLATFORM_Z,
  STEP,
  TILE,
  beltEnd,
  beltStart,
  childLineOf,
  crateLevel,
  deckDepth,
  easeOut,
  isoViewBox,
  laneLines,
  laneOffsets,
  stationX,
  loopPoint,
  placeBranches,
  placeCrates,
  railPoint,
  roundBadge,
  runLoop,
  runName,
  stationHealth,
  stationName,
  stationWidth,
  subStationX,
  subStep,
  summarize,
  towerDecks,
  towerSpan,
  truncate,
  unionViewBox,
  workerAngle,
  workerPoint,
  workerSpeed,
  type CratePose,
  type PelletPose,
  type Vec3,
  type ViewBox,
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
 * Shapes. Rounds climb: a back-edge loop raises a tower of decks over the
 * stages it spans, one per round, and a run rides its round's deck, so a run
 * that went round four times sits visibly higher; the deck a run is on now is
 * lit and earlier rounds are ghosts. Sending work back moves its crate up the
 * return ramp to the earlier stage, one level higher; every ramp carries its
 * bound, and a run's badge turns amber one round from it. A retry is a hoop
 * over its own stage. A fanout's branches ride raised rails, lit while they
 * run, and each rolls on to the join block when it settles; the join shows
 * how many have. A supervisor's workers orbit its hub on a tile while busy,
 * as fast as their load. A nested workflow is a stacked block that opens to
 * show its child stages, and the child run, on a sub-platform.
 *
 * One requestAnimationFrame loop writes crate, pellet and worker positions
 * through refs; React renders once per snapshot. Reduced motion draws every
 * crate, pellet and worker where the snapshot puts it, with no tween or
 * orbit, and the decks, badges, rails and counts carry the rest.
 */

export const WORKFLOW_ISOMETRIC_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Run moving (crate) / stage working" },
  { glyph: "signal", color: HEALTH_COLOR.degraded, label: "Held at a gate: booth shut, breathing" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Run failed at that machine" },
  { glyph: "bar", color: HEALTH_COLOR.degraded, label: "Backlog waiting at an intake" },
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Belt speed = throughput" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle / finished" },
  {
    glyph: "return",
    color: HEALTH_COLOR.degraded,
    label:
      "Return ramp: work sent back climbs one level to the earlier stage; its bound is on the ramp",
  },
  {
    glyph: "round",
    color: HEALTH_COLOR.ok,
    label: "Deck per round over the looped stages: the current round lit, earlier rounds ghosted",
  },
  WORKFLOW_SHAPE_LEGEND.roundBadge,
  WORKFLOW_SHAPE_LEGEND.roundNearBound,
  {
    glyph: "siding",
    color: HEALTH_COLOR.degraded,
    label: "Hoop over a stage: a retry of that stage, labelled with its attempt bound",
  },
  {
    glyph: "join",
    color: HEALTH_COLOR.ok,
    label:
      "Raised rails: fan-out branches, lit while running; each rolls to the join block when it settles",
  },
  {
    glyph: "swarm",
    color: HEALTH_COLOR.ok,
    label:
      "Supervisor tile: busy workers orbit the hub, faster with more load; idle workers stand still",
  },
  {
    glyph: "nested",
    color: HEALTH_COLOR.ok,
    label: "Stacked block: a child workflow; open it to see its stages on a sub-platform",
  },
];

const UNIT = 20;
const TWEEN_MS = 900;
const CRATE = 0.5;
const CHILD_CRATE = 0.26;
const PELLET = 0.24;
const HUB = 0.8;
const HUB_H = 0.9;
const WORKER = 0.3;

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

const at = (p: Vec3): Point => iso(p.x, p.y, p.z, UNIT);
const translate = ([sx, sy]: Point) => `translate(${sx.toFixed(1)},${sy.toFixed(1)})`;

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

function FocusRing({
  x,
  y,
  w,
  d,
  h,
  z = 0,
}: {
  x: number;
  y: number;
  w: number;
  d: number;
  h: number;
  z?: number;
}) {
  const f = isoBox(x, y, w, d, h, UNIT, z);
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

/** A focusable, selectable piece of the picture (a station or a run). */
function Selectable({
  label,
  onSelect,
  className,
  style,
  children,
  ...rest
}: {
  label: string;
  onSelect?: () => void;
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
} & Omit<React.SVGProps<SVGGElement>, "onSelect">) {
  return (
    <g
      {...rest}
      tabIndex={0}
      role={onSelect ? "button" : "img"}
      aria-label={label}
      onClick={onSelect}
      onKeyDown={onSelect ? onActivate(onSelect) : undefined}
      className={cn("group outline-none", onSelect && "cursor-pointer", className)}
      style={style}
    >
      <title>{label}</title>
      {children}
    </g>
  );
}

/* ---------- The animation engine: one rAF loop writing through refs ---------- */

interface Mover {
  to: CratePose;
  fromPt: Vec3;
  /** Set when the tween follows the same track: the path parameter it starts from. */
  fromS?: number;
  cur: Vec3;
  curS?: number;
}

interface Orbiter {
  id: string;
  cx: number;
  y: number;
  rest: number;
  speed: number;
  angle: number;
}

function createEngine() {
  const movers = new Map<string, Mover>();
  const moverEls = new Map<string, SVGGElement>();
  const orbiters = new Map<string, Orbiter>();
  const workerEls = new Map<string, SVGGElement>();
  const tetherEls = new Map<string, SVGLineElement>();
  let start = 0;
  let tweening = false;
  let raf = 0;
  let last = 0;
  let frameBox: { key: string; vb: ViewBox } | null = null;
  const hasRaf = () =>
    typeof window !== "undefined" && typeof window.requestAnimationFrame === "function";

  const paintMovers = () => {
    for (const [id, m] of movers) moverEls.get(id)?.setAttribute("transform", translate(at(m.cur)));
  };
  const paintOrbiters = () => {
    for (const o of orbiters.values()) {
      const p = at(workerPoint(o.cx, o.y, o.angle));
      workerEls.get(o.id)?.setAttribute("transform", translate(p));
      const tether = tetherEls.get(o.id);
      if (tether) {
        tether.setAttribute("x2", p[0].toFixed(1));
        tether.setAttribute("y2", p[1].toFixed(1));
      }
    }
  };
  const settle = () => {
    for (const m of movers.values()) {
      m.cur = { x: m.to.x, y: m.to.y, z: m.to.z };
      m.curS = m.to.track?.s;
    }
    tweening = false;
  };

  const frame = (now: number) => {
    raf = 0;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    let again = false;
    if (tweening) {
      const k = easeOut((now - start) / TWEEN_MS);
      for (const m of movers.values()) {
        if (m.fromS !== undefined && m.to.track) {
          const s = m.fromS + (m.to.track.s - m.fromS) * k;
          m.cur = m.to.track.point(s);
          m.curS = s;
        } else {
          m.cur = {
            x: m.fromPt.x + (m.to.x - m.fromPt.x) * k,
            y: m.fromPt.y + (m.to.y - m.fromPt.y) * k,
            z: m.fromPt.z + (m.to.z - m.fromPt.z) * k,
          };
          m.curS = k >= 1 ? m.to.track?.s : undefined;
        }
      }
      paintMovers();
      if (k < 1) again = true;
      else tweening = false;
    }
    let orbiting = false;
    for (const o of orbiters.values()) {
      if (o.speed <= 0) continue;
      o.angle += o.speed * dt;
      orbiting = true;
    }
    if (orbiting) {
      paintOrbiters();
      again = true;
    }
    if (again) raf = window.requestAnimationFrame(frame);
  };

  const kick = () => {
    if (raf || !hasRaf()) return;
    last = performance.now();
    raf = window.requestAnimationFrame(frame);
  };

  return {
    moverEls,
    workerEls,
    tetherEls,
    register<E extends Element>(map: Map<string, E>, id: string, el: E | null) {
      if (el) map.set(id, el);
      else map.delete(id);
    },
    /** The view box, grown to hold everything it has held while the lanes stay the same. */
    frameBox(key: string, vb: ViewBox): ViewBox {
      const next = frameBox && frameBox.key === key ? unionViewBox(frameBox.vb, vb) : vb;
      frameBox = { key, vb: next };
      return next;
    },
    /** New targets: tween from where each thing is drawn now (along its track, when it stays on one). */
    setMovers(poses: Map<string, CratePose>, reduced: boolean) {
      for (const id of [...movers.keys()]) if (!poses.has(id)) movers.delete(id);
      let moving = false;
      for (const [id, p] of poses) {
        const prev = movers.get(id);
        // A run that restarted at the top of its line, or moved lane, jumps.
        const jump =
          !prev ||
          reduced ||
          prev.to.lane !== p.lane ||
          !!prev.to.child !== !!p.child ||
          (!p.track && !prev.to.track && p.x < prev.cur.x - STEP);
        if (jump) {
          movers.set(id, { to: p, fromPt: p, cur: { x: p.x, y: p.y, z: p.z }, curS: p.track?.s });
          continue;
        }
        const same = !!p.track && prev.to.track?.id === p.track.id;
        const cur = { ...prev.cur };
        movers.set(id, {
          to: p,
          fromPt: cur,
          fromS: same ? (prev.curS ?? prev.to.track!.s) : undefined,
          cur,
          curS: same ? prev.curS : undefined,
        });
        if (Math.abs(cur.x - p.x) + Math.abs(cur.y - p.y) + Math.abs(cur.z - p.z) > 1e-3)
          moving = true;
      }
      start = typeof performance !== "undefined" ? performance.now() : 0;
      tweening = moving;
      if (moving && !hasRaf()) settle();
      paintMovers();
      if (tweening) kick();
    },
    /** Workers: keep each one's angle, change its speed; reduced motion rests them all. */
    setOrbiters(list: Omit<Orbiter, "angle">[], reduced: boolean) {
      const ids = new Set(list.map((o) => o.id));
      for (const id of [...orbiters.keys()]) if (!ids.has(id)) orbiters.delete(id);
      for (const o of list) {
        const prev = orbiters.get(o.id);
        orbiters.set(o.id, {
          ...o,
          speed: reduced ? 0 : o.speed,
          angle: reduced || !prev ? o.rest : prev.angle,
        });
      }
      paintOrbiters();
      if (list.some((o) => o.speed > 0) && !reduced) kick();
    },
    stop() {
      if (raf) window.cancelAnimationFrame(raf);
      raf = 0;
    },
  };
}

type Engine = ReturnType<typeof createEngine>;

/* ---------- Stations ---------- */

interface StationProps {
  snapshot: WorkflowSnapshot;
  line: WorkflowLine;
  j: number;
  y: number;
  engine: Engine;
  open: ReadonlySet<string>;
  onToggle: (stationId: string) => void;
  onSelect?: (lineId: string, stationId: string) => void;
}

function Station({ snapshot, line, j, y, engine, open, onToggle, onSelect }: StationProps) {
  const station = line.stations[j];
  const health = stationHealth(snapshot, line, j);
  const cx = stationX(line, j);
  const gate = station.kind === "gate";
  const w = stationWidth(line, j);
  const d = gate ? BELT + 0.4 : MACHINE;
  const h = gate ? 1.35 : station.kind === "join" ? 0.8 : station.kind === "supervisor" ? HUB_H : 1;
  const x0 = cx - w / 2;
  const y0 = y - d / 2;
  const [lx, ly] = iso(cx, y + d / 2 + 0.25, 0, UNIT);
  const select = onSelect ? () => onSelect(line.id, station.id) : undefined;
  const label = stationName(snapshot, line, j);
  const flicker = health === "failing" ? MOTION_CLASS.flicker : undefined;
  const topLabel = (text: string, color: string, z = h) => {
    const [tx, ty] = iso(cx, y, z, UNIT);
    return (
      <text
        x={tx}
        y={ty + 3}
        textAnchor="middle"
        fontSize="8"
        className="font-mono"
        fill={color}
        aria-hidden
      >
        {text}
      </text>
    );
  };

  let body: React.ReactNode;
  let ring = <FocusRing x={x0 - 0.1} y={y0 - 0.1} w={w + 0.2} d={d + 0.2} h={h} />;
  switch (station.kind) {
    case "gate": {
      // The booth's door on its front face: shut while holding, a gap when open.
      const held = health === "degraded";
      const gap = held ? 0 : 0.28;
      const a = iso(cx - gap / 2, y + d / 2, 0.05, UNIT);
      const b = iso(cx - gap / 2, y + d / 2, h - 0.2, UNIT);
      const c2 = iso(cx + gap / 2, y + d / 2, 0.05, UNIT);
      const d2 = iso(cx + gap / 2, y + d / 2, h - 0.2, UNIT);
      const light = iso(cx, y, h, UNIT);
      body = (
        <>
          <Box x={x0} y={y0} w={w} d={d} h={h} health={health} topClass={flicker} />
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
      break;
    }
    case "fanout": {
      const n = station.branches.length;
      body = (
        <>
          <Box x={x0} y={y0} w={w} d={d} h={h} health={health} topClass={flicker} />
          {topLabel(
            n ? `×${n}` : "×n",
            health === "idle" ? "var(--muted-foreground)" : "var(--foreground)"
          )}
        </>
      );
      break;
    }
    case "join": {
      const fan = line.stations[station.waitsOn];
      const r = fan?.kind === "fanout" ? joinReady(fan) : null;
      const waiting = !!(fan?.kind === "fanout" && fan.runId && r && !r.ready);
      body = (
        <>
          <Box
            x={x0}
            y={y0}
            w={w}
            d={d}
            h={h}
            health={health}
            topClass={flicker ?? (waiting ? MOTION_CLASS.breathe : undefined)}
          />
          {r &&
            r.total > 0 &&
            topLabel(
              `${r.settled}/${r.total}`,
              waiting ? "var(--foreground)" : "var(--muted-foreground)"
            )}
        </>
      );
      break;
    }
    case "supervisor": {
      // The sub-tasks of the runs here: done of total, on the hub.
      const runs = snapshot.trains.filter(
        (t) => t.lineId === line.id && t.at === j && t.subtasks && t.state === "moving"
      );
      const total = runs.reduce((n, t) => n + t.subtasks!.total, 0);
      const done = runs.reduce((n, t) => n + t.subtasks!.done, 0);
      body = (
        <>
          <Supervisor station={station} cx={cx} y={y} health={health} engine={engine} />
          {total > 0 && topLabel(`${done}/${total}`, "var(--foreground)", HUB_H)}
        </>
      );
      ring = (
        <FocusRing x={cx - HUB / 2 - 0.1} y={y - HUB / 2 - 0.1} w={HUB + 0.2} d={HUB + 0.2} h={h} />
      );
      break;
    }
    case "workflow":
      body = (
        <Nested
          snapshot={snapshot}
          line={line}
          station={station}
          j={j}
          y={y}
          health={health}
          flicker={flicker}
          isOpen={open.has(station.id)}
        />
      );
      ring = <FocusRing x={x0 - 0.1} y={y0 - 0.1} w={w + 0.2} d={d + 0.2} h={1} />;
      break;
    default:
      body = <Box x={x0} y={y0} w={w} d={d} h={h} health={health} topClass={flicker} />;
  }

  return (
    <g>
      <Selectable label={label} onSelect={select} style={glow(health)}>
        {body}
        {ring}
        <text
          x={lx}
          y={ly + 12}
          textAnchor="middle"
          fontSize="9"
          className="fill-muted-foreground font-mono"
        >
          {truncate(station.name, 10)}
        </text>
      </Selectable>
      {station.kind === "workflow" && (
        <NestedToggle
          snapshot={snapshot}
          station={station}
          cx={cx}
          y={y}
          isOpen={open.has(station.id)}
          onToggle={() => onToggle(station.id)}
        />
      )}
    </g>
  );
}

function Supervisor({
  station,
  cx,
  y,
  health,
  engine,
}: {
  station: SupervisorStation;
  cx: number;
  y: number;
  health: Health;
  engine: Engine;
}) {
  const hubTop = iso(cx, y, HUB_H, UNIT);
  const n = station.workers.length;
  return (
    <>
      <Box
        x={cx - HUB / 2}
        y={y - HUB / 2}
        w={HUB}
        d={HUB}
        h={HUB_H}
        health={health}
        topClass={health === "failing" ? MOTION_CLASS.flicker : undefined}
      />
      {/* Tethers after the hub, so a worker behind it still shows its link. */}
      {station.workers.map((w, i) => {
        if (!w.busy) return null;
        const rest = at(workerPoint(cx, y, workerAngle(i, n)));
        return (
          <line
            key={`t-${w.id}`}
            ref={(el) => engine.register(engine.tetherEls, w.id, el)}
            x1={hubTop[0].toFixed(1)}
            y1={hubTop[1].toFixed(1)}
            x2={rest[0].toFixed(1)}
            y2={rest[1].toFixed(1)}
            stroke={HEALTH_COLOR.ok}
            strokeWidth={(0.75 + w.load * 1.75).toFixed(2)}
            opacity="0.7"
          />
        );
      })}
      {station.workers.map((w, i) => (
        <g
          key={w.id}
          ref={(el) => engine.register(engine.workerEls, w.id, el)}
          transform={translate(at(workerPoint(cx, y, workerAngle(i, n))))}
        >
          <title>{`${w.label}: ${w.busy ? "busy" : "idle"}, load ${Math.round(w.load * 100)}%`}</title>
          <Box
            x={-WORKER / 2}
            y={-WORKER / 2}
            w={WORKER}
            d={WORKER}
            h={WORKER}
            health={w.busy ? "ok" : "idle"}
          />
        </g>
      ))}
    </>
  );
}

/** The supervisor's tile, drawn under the belt. */
function SupervisorTile({ cx, y }: { cx: number; y: number }) {
  const f = isoBox(cx - TILE / 2, y - TILE / 2, TILE, TILE, 0.04, UNIT);
  return (
    <polygon
      points={f.top}
      fill="color-mix(in oklab, var(--muted-foreground) 8%, var(--card))"
      stroke="var(--border)"
      strokeWidth="0.75"
      strokeDasharray="3 3"
    />
  );
}

function Nested({
  snapshot,
  line,
  station,
  j,
  y,
  health,
  flicker,
  isOpen,
}: {
  snapshot: WorkflowSnapshot;
  line: WorkflowLine;
  station: NestedStation;
  j: number;
  y: number;
  health: Health;
  flicker?: string;
  isOpen: boolean;
}) {
  const cx = stationX(line, j);
  const child = childLineOf(snapshot, station);
  const parent = snapshot.trains.find((t) => t.lineId === line.id && t.at === j && t.childRunId);
  const run = parent && snapshot.trains.find((t) => t.id === parent.childRunId);
  const [tx, ty] = iso(cx, y, 1, UNIT);
  return (
    <>
      <Box
        x={cx - MACHINE / 2}
        y={y - MACHINE / 2}
        w={MACHINE}
        d={MACHINE}
        h={0.55}
        health={health}
      />
      <Box
        x={cx - 0.5}
        y={y - 0.5}
        w={1}
        d={1}
        h={0.45}
        z={0.55}
        health={health}
        topClass={flicker}
      />
      {!isOpen && child && run && run.state !== "done" && (
        <text
          x={tx}
          y={ty + 3}
          textAnchor="middle"
          fontSize="8"
          className="fill-foreground font-mono"
          aria-hidden
        >
          {`${run.at + 1}/${child.stations.length}`}
        </text>
      )}
    </>
  );
}

function NestedToggle({
  snapshot,
  station,
  cx,
  y,
  isOpen,
  onToggle,
}: {
  snapshot: WorkflowSnapshot;
  station: NestedStation;
  cx: number;
  y: number;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const child = childLineOf(snapshot, station);
  if (!child) return null;
  const [hx, hy] = iso(cx + MACHINE / 2 + 0.3, y - MACHINE / 2 - 0.3, 0, UNIT);
  const label = `${isOpen ? "Close" : "Open"} ${child.name}, the child workflow run by ${station.name}`;
  return (
    <g
      tabIndex={0}
      role="button"
      aria-expanded={isOpen}
      aria-label={label}
      onClick={onToggle}
      onKeyDown={onActivate(onToggle)}
      className="group cursor-pointer outline-none"
    >
      <title>{label}</title>
      <circle cx={hx} cy={hy} r="6" fill="var(--card)" stroke="var(--border)" />
      <circle
        cx={hx}
        cy={hy}
        r="8"
        fill="none"
        stroke="var(--ring)"
        strokeWidth="2"
        className="opacity-0 group-focus-visible:opacity-100"
      />
      <path
        d={
          isOpen
            ? `M${hx - 3},${hy} H${hx + 3}`
            : `M${hx - 3},${hy} H${hx + 3} M${hx},${hy - 3} V${hy + 3}`
        }
        stroke="var(--foreground)"
        strokeWidth="1.25"
      />
    </g>
  );
}

/** An open nested workflow: its child stages on a sub-platform over the block. */
function NestedPlatform({
  snapshot,
  line,
  station,
  j,
  y,
  onSelect,
}: {
  snapshot: WorkflowSnapshot;
  line: WorkflowLine;
  station: NestedStation;
  j: number;
  y: number;
  onSelect?: (lineId: string, stationId: string) => void;
}) {
  const child = childLineOf(snapshot, station);
  if (!child) return null;
  const cx = stationX(line, j);
  const n = child.stations.length;
  const w = n * subStep(n) + 0.4;
  const slab = isoBox(cx - w / 2, y - 0.55, w, 1.1, DECK, UNIT, PLATFORM_Z);
  const post = [iso(cx, y, 1, UNIT), iso(cx, y, PLATFORM_Z, UNIT)];
  const mini = 0.5;
  return (
    <g>
      <title>{`${child.name}: the child workflow's stages`}</title>
      <polyline points={points(post)} stroke="var(--border)" strokeWidth="1.25" />
      <polygon points={slab.left} fill={shade("var(--card)", 0.3)} stroke="var(--border)" />
      <polygon points={slab.right} fill={shade("var(--card)", 0.45)} stroke="var(--border)" />
      <polygon
        points={slab.top}
        fill="color-mix(in oklab, var(--muted-foreground) 10%, var(--card))"
        stroke="var(--border)"
      />
      {child.stations.map((st, i) => {
        const sx = subStationX(cx, n, i);
        const health = stationHealth(snapshot, child, i);
        // Names stand above the stages, clear of the next stage on the platform.
        const [lx, ly] = iso(sx, y, PLATFORM_Z + DECK + 0.85, UNIT);
        return (
          <Selectable
            key={st.id}
            label={stationName(snapshot, child, i)}
            onSelect={onSelect ? () => onSelect(child.id, st.id) : undefined}
            style={glow(health)}
          >
            <Box
              x={sx - mini / 2}
              y={y - mini / 2}
              w={mini}
              d={mini}
              h={0.4}
              z={PLATFORM_Z + DECK}
              health={health}
              topClass={health === "failing" ? MOTION_CLASS.flicker : undefined}
            />
            <FocusRing
              x={sx - mini / 2 - 0.08}
              y={y - mini / 2 - 0.08}
              w={mini + 0.16}
              d={mini + 0.16}
              h={0.4}
              z={PLATFORM_Z + DECK}
            />
            <text
              x={lx}
              y={ly}
              textAnchor="middle"
              fontSize="7"
              className="fill-muted-foreground font-mono"
            >
              {truncate(st.name, 8)}
            </text>
          </Selectable>
        );
      })}
    </g>
  );
}

/* ---------- Shapes on the line: tower decks, return tracks, rails ---------- */

function Deck({
  line,
  y,
  level,
  lit,
}: {
  line: WorkflowLine;
  y: number;
  level: number;
  lit: boolean;
}) {
  const span = towerSpan(line)!;
  const x0 = stationX(line, span.lo) - MACHINE / 2 - 0.3;
  const x1 = stationX(line, span.hi) + MACHINE / 2 + 0.3;
  const { y0, y1 } = deckDepth(line, y);
  const z = level * LEVEL;
  const f = isoBox(x0, y0, x1 - x0, y1 - y0, DECK, UNIT, z);
  const stroke = lit ? HEALTH_COLOR.ok : "var(--border)";
  const below = level > 1 ? (level - 1) * LEVEL + DECK : 0;
  const posts = [x0, x1].map((px) => [iso(px, y1, below, UNIT), iso(px, y1, z, UNIT)]);
  const [lx, ly] = iso(x1 + 0.15, y0, z + DECK, UNIT);
  return (
    <g opacity={lit ? 1 : 0.35}>
      <title>{`Round ${level + 1}${lit ? ": a run is on this round now" : ": an earlier round"}`}</title>
      {posts.map((p, k) => (
        <polyline key={k} points={points(p)} stroke="var(--border)" strokeWidth="1" />
      ))}
      <polygon
        points={f.left}
        fill={shade("var(--card)", 0.3)}
        stroke={stroke}
        strokeWidth="0.75"
      />
      <polygon
        points={f.top}
        fill={lit ? "color-mix(in oklab, var(--success) 16%, var(--card))" : "var(--card)"}
        fillOpacity={lit ? 0.5 : 0.35}
        stroke={stroke}
        strokeWidth="0.75"
        strokeDasharray={lit ? undefined : "3 3"}
      />
      {lit &&
        Array.from({ length: span.hi - span.lo + 1 }, (_, k) => {
          // The looped stations' footprints, repeated on this round's deck.
          const j = span.lo + k;
          const w = stationWidth(line, j);
          const foot = isoBox(
            stationX(line, j) - w / 2,
            y - MACHINE / 2,
            w,
            MACHINE,
            0,
            UNIT,
            z + DECK
          );
          return (
            <polygon
              key={j}
              points={foot.top}
              fill="none"
              stroke={HEALTH_COLOR.ok}
              strokeWidth="0.5"
              strokeDasharray="2 2"
            />
          );
        })}
      <text
        x={lx}
        y={ly + 3}
        textAnchor="start"
        fontSize="8"
        className={cn("font-mono", lit ? "fill-foreground" : "fill-muted-foreground")}
      >
        {`round ${level + 1}`}
      </text>
    </g>
  );
}

function path(fn: (s: number) => Vec3, samples = 12): string {
  return points(Array.from({ length: samples + 1 }, (_, k) => at(fn(k / samples))));
}

/** Return ramps and retry hoops, each labelled with its bound; lit under a run riding it. */
function LoopTracks({
  snapshot,
  line,
  y,
}: {
  snapshot: WorkflowSnapshot;
  line: WorkflowLine;
  y: number;
}) {
  const loops = line.loops ?? [];
  if (loops.length === 0) return null;
  const riders = snapshot.trains.filter((t) => t.lineId === line.id && t.onLoopId);
  return (
    <g>
      {loops.map((loop) => {
        const retry = loop.to === loop.from;
        const template = (s: number) => loopPoint(line, loop, 0, s, y);
        const foot = template(1);
        const levels = new Set(
          riders.filter((t) => t.onLoopId === loop.id).map((t) => crateLevel(line, t))
        );
        return (
          <g key={loop.id}>
            <title>{loopLabel(line, loop)}</title>
            {!retry && (
              <polyline
                points={points([at(foot), at({ ...foot, z: 0 })])}
                stroke="var(--border)"
                strokeWidth="1"
              />
            )}
            <polyline
              points={path(template)}
              fill="none"
              stroke={HEALTH_COLOR.degraded}
              strokeWidth="1.5"
              strokeDasharray="4 3"
              opacity="0.55"
            />
            {[...levels].map((level) => (
              <polyline
                key={level}
                points={path((s) => loopPoint(line, loop, level, s, y))}
                fill="none"
                stroke={HEALTH_COLOR.degraded}
                strokeWidth="2.25"
              />
            ))}
          </g>
        );
      })}
    </g>
  );
}

/** Each loop's bound, drawn over everything so no deck or machine hides it. */
function LoopTags({ line, y }: { line: WorkflowLine; y: number }) {
  return (
    <>
      {(line.loops ?? []).map((loop) => {
        const retry = loop.to === loop.from;
        const p = loopPoint(line, loop, 0, retry ? 0.5 : 0.2, y);
        const [tx, ty] = at({ ...p, z: p.z + (retry ? 0.35 : 0.45) });
        return (
          <text
            key={loop.id}
            x={tx}
            y={ty}
            textAnchor="middle"
            fontSize="8"
            fill={HEALTH_COLOR.degraded}
            className="font-mono"
          >
            <title>{loopLabel(line, loop)}</title>
            {`max ${loop.maxRounds}`}
          </text>
        );
      })}
    </>
  );
}

const RAIL_STROKE: Record<string, string> = {
  queued: "var(--border)",
  running: HEALTH_COLOR.ok,
  succeeded: HEALTH_COLOR.ok,
  failed: HEALTH_COLOR.failing,
};

function Rails({
  line,
  station,
  j,
  y,
  motion,
  flowParticles,
  pellets,
  engine,
}: {
  line: WorkflowLine;
  station: FanoutStation;
  j: number;
  y: number;
  motion: "full" | "reduced";
  flowParticles: boolean;
  pellets: Map<string, PelletPose>;
  engine: Engine;
}) {
  const n = station.branches.length;
  const live = !!station.runId;
  if (n === 0) {
    // No run has fanned out yet: the rails' shape, ghosted; the count is decided per run.
    return (
      <g opacity="0.4">
        <title>{`${station.name}: branches decided per run`}</title>
        {[0, 1, 2].map((b) => (
          <polyline
            key={b}
            points={path((s) => railPoint(line, j, b, 3, s, y))}
            fill="none"
            stroke="var(--border)"
            strokeWidth="1.25"
            strokeDasharray="3 3"
          />
        ))}
      </g>
    );
  }
  return (
    <g>
      {station.branches.map((b, k) => {
        const running = live && b.state === "running";
        const flowing = running && flowParticles && motion === "full";
        return (
          <polyline
            key={b.id}
            points={path((s) => railPoint(line, j, k, n, s, y))}
            fill="none"
            stroke={live ? RAIL_STROKE[b.state] : "var(--border)"}
            strokeWidth={running ? 2 : 1.25}
            opacity={live ? (b.state === "succeeded" ? 0.55 : 1) : 0.45}
            strokeDasharray={flowing ? "2 10" : live ? undefined : "3 3"}
            className={flowing ? MOTION_CLASS.flow : undefined}
            style={
              flowing
                ? ({ "--viz-flow-duration": flowDuration(0.6) } as React.CSSProperties)
                : undefined
            }
          />
        );
      })}
      {station.branches.map((b) => {
        const p = pellets.get(b.id);
        if (!p) return null;
        const health: Health = !live
          ? "idle"
          : b.state === "failed"
            ? "failing"
            : b.state === "queued"
              ? "idle"
              : "ok";
        return (
          <g
            key={b.id}
            ref={(el) => engine.register(engine.moverEls, `b:${b.id}`, el)}
            opacity={live ? 1 : 0.45}
          >
            <title>{`${b.label}: ${b.state}${live ? "" : " (last run)"}`}</title>
            <Box
              x={-PELLET / 2}
              y={-PELLET / 2}
              w={PELLET}
              d={PELLET}
              h={0.2}
              health={health}
              topClass={health === "failing" ? MOTION_CLASS.flicker : undefined}
            />
          </g>
        );
      })}
    </g>
  );
}

/* ---------- Runs ---------- */

function Crate({
  snapshot,
  line,
  t,
  child,
  engine,
  onSelect,
}: {
  snapshot: WorkflowSnapshot;
  line: WorkflowLine;
  t: WorkflowTrain;
  child: boolean;
  engine: Engine;
  onSelect?: (id: string) => void;
}) {
  const size = child ? CHILD_CRATE : CRATE;
  const h = size * 0.84;
  const health = CRATE_HEALTH[t.state];
  const badge = roundBadge(line, t);
  const loop = runLoop(line, t);
  const warn = !!badge && !!loop && isNearBound(t, loop);
  const badgeColor = warn ? HEALTH_COLOR.degraded : HEALTH_COLOR.ok;
  const [, by] = iso(0, 0, h, UNIT);
  return (
    <Selectable
      label={runName(snapshot, line, t)}
      onSelect={onSelect ? () => onSelect(t.id) : undefined}
      ref={(el: SVGGElement | null) => engine.register(engine.moverEls, t.id, el)}
      opacity={t.state === "done" ? 0.45 : 1}
      style={t.state === "failed" ? glow("failing") : undefined}
    >
      <Box
        x={-size / 2}
        y={-size / 2}
        w={size}
        d={size}
        h={h}
        health={health}
        topClass={t.state === "failed" ? MOTION_CLASS.flicker : undefined}
      />
      <FocusRing
        x={-size / 2 - 0.1}
        y={-size / 2 - 0.1}
        w={size + 0.2}
        d={size + 0.2}
        h={h + 0.12}
      />
      {badge && (
        <g transform={`translate(0,${(by - 9).toFixed(1)})`} aria-hidden>
          <rect
            x="-11"
            y="-5.5"
            width="22"
            height="11"
            rx="5.5"
            fill="var(--card)"
            stroke={badgeColor}
          />
          <text y="3" textAnchor="middle" fontSize="8" fill={badgeColor} className="font-mono">
            {badge}
          </text>
        </g>
      )}
    </Selectable>
  );
}

/* ---------- The view ---------- */

export interface WorkflowIsometricProps extends WorkflowViewProps {
  /** Nested workflow station ids shown open at first; the viewer opens and closes them. */
  defaultOpen?: string[];
}

export function WorkflowIsometric({
  snapshot,
  motion,
  flowParticles = true,
  onSelectRun,
  onSelectStation,
  className,
  defaultOpen,
}: WorkflowIsometricProps) {
  const [engine] = React.useState(createEngine);
  const [open, setOpen] = React.useState<ReadonlySet<string>>(() => new Set(defaultOpen));
  const toggle = React.useCallback(
    (id: string) =>
      setOpen((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
    []
  );
  const reduced = motion === "reduced";
  const lanes = React.useMemo(() => laneLines(snapshot), [snapshot]);
  const ys = React.useMemo(() => laneOffsets(lanes), [lanes]);
  const laneKey = lanes.map((l) => `${l.id}:${l.stations.length}`).join("|");
  const vb = React.useMemo(
    () => engine.frameBox(laneKey, isoViewBox(snapshot, UNIT, open)),
    [engine, laneKey, snapshot, open]
  );
  const crates = React.useMemo(() => placeCrates(snapshot, open), [snapshot, open]);
  const pellets = React.useMemo(() => placeBranches(snapshot), [snapshot]);
  const lineById = React.useMemo(
    () => new Map(snapshot.lines.map((l) => [l.id, l])),
    [snapshot.lines]
  );

  // Crates and pellets tween from where they are drawn now to the new
  // snapshot's position; workers keep orbiting at their load's speed.
  React.useLayoutEffect(() => {
    const poses = new Map<string, CratePose>(crates);
    for (const [id, p] of pellets) poses.set(`b:${id}`, p);
    engine.setMovers(poses, reduced);
  }, [engine, crates, pellets, reduced]);

  React.useLayoutEffect(() => {
    engine.setOrbiters(
      lanes.flatMap((line, li) =>
        line.stations.flatMap((st, j) =>
          st.kind === "supervisor"
            ? st.workers.map((w, i) => ({
                id: w.id,
                cx: stationX(line, j),
                y: ys[li],
                rest: workerAngle(i, st.workers.length),
                speed: workerSpeed(w),
              }))
            : []
        )
      ),
      reduced
    );
  }, [engine, lanes, ys, reduced]);

  React.useEffect(() => () => engine.stop(), [engine]);

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
      {lanes.map((line, li) => {
        const y = ys[li];
        const x0 = beltStart();
        const x1 = beltEnd(line);
        const belt = isoBox(x0, y - BELT / 2, x1 - x0, BELT, BELT_H, UNIT);
        const [nx, ny] = iso(x0 - 0.2, y, 0, UNIT);
        // Painter's order: by level, then floors before what stands on them, then far to near.
        const items: { layer: number; order: number; depth: number; node: React.ReactNode }[] = [];

        line.stations.forEach((st, j) => {
          items.push({
            layer: 0,
            order: 1,
            depth: stationX(line, j) + y,
            node: (
              <Station
                key={st.id}
                snapshot={snapshot}
                line={line}
                j={j}
                y={y}
                engine={engine}
                open={open}
                onToggle={toggle}
                onSelect={onSelectStation}
              />
            ),
          });
          if (st.kind === "fanout")
            items.push({
              layer: 0,
              order: 2,
              depth: stationX(line, j) + y,
              node: (
                <Rails
                  key={`rails-${st.id}`}
                  line={line}
                  station={st}
                  j={j}
                  y={y}
                  motion={motion}
                  flowParticles={flowParticles}
                  pellets={pellets}
                  engine={engine}
                />
              ),
            });
          if (st.kind === "workflow" && open.has(st.id))
            items.push({
              layer: 2,
              order: 0,
              depth: stationX(line, j) + y,
              node: (
                <NestedPlatform
                  key={`platform-${st.id}`}
                  snapshot={snapshot}
                  line={line}
                  station={st}
                  j={j}
                  y={y}
                  onSelect={onSelectStation}
                />
              ),
            });
        });

        for (const d of towerDecks(snapshot, line))
          items.push({
            layer: d.level,
            order: 0,
            depth: 0,
            node: <Deck key={`deck-${d.level}`} line={line} y={y} level={d.level} lit={d.lit} />,
          });

        for (const t of snapshot.trains) {
          const p = crates.get(t.id);
          const own = lineById.get(t.lineId);
          if (!p || p.lane !== line.id || !own) continue;
          items.push({
            layer: p.child ? 2 : Math.max(0, Math.floor((p.z + 0.25) / LEVEL)),
            order: 1,
            depth: p.x + p.y,
            node: (
              <Crate
                key={t.id}
                snapshot={snapshot}
                line={own}
                t={t}
                child={!!p.child}
                engine={engine}
                onSelect={onSelectRun}
              />
            ),
          });
        }
        items.sort((a, b) => a.layer - b.layer || a.order - b.order || a.depth - b.depth);

        return (
          <g key={line.id}>
            <LoopTracks snapshot={snapshot} line={line} y={y} />
            {line.stations.map((st, j) =>
              st.kind === "supervisor" ? (
                <SupervisorTile key={st.id} cx={stationX(line, j)} y={y} />
              ) : null
            )}
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
              const a = stationX(line, s.from) + stationWidth(line, s.from) / 2 + 0.1;
              const b = stationX(line, s.from + 1) - stationWidth(line, s.from + 1) / 2 - 0.1;
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
            <LoopTags line={line} y={y} />
          </g>
        );
      })}
    </svg>
  );
}
