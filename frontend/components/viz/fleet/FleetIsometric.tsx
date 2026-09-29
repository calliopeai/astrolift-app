"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { FleetCluster, FleetViewProps } from "../core/fleet-model";
import { isoBox, iso, points, shade } from "../core/iso";
import {
  HEALTH_COLOR,
  HEALTH_GLOWS,
  HEALTH_LABEL,
  MOTION_CLASS,
  type Health,
} from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import {
  BOX,
  SLAB,
  boxHeight,
  fleetAriaLabel,
  fleetBounds,
  layoutFleet,
  recentStarts,
  summarizeFleet,
  truncate,
} from "./fleet-isometric-layout";

/**
 * Isometric fleet (spec 44 viz addendum): each cluster is a platform slab,
 * each agent a box on it. Height is load, the top face is health, sides are
 * the same hue shaded. Healthy and degraded boxes glow under the top face,
 * failing boxes flicker, idle boxes sit low and dark. A spark rises off a box
 * when a run starts. Height changes tween on one shared rAF loop that only
 * touches boxes whose load changed.
 *
 * Reduced motion: boxes jump to their final heights, nothing flickers, and a
 * run that started in the last few seconds is a still pip on the box top
 * instead of a rising spark. Failing boxes keep a red outline so the still
 * frame says what the flicker said.
 */

export const FLEET_ISOMETRIC_LEGEND: LegendItem[] = [
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Healthy (height is load)" },
  { glyph: "glow", color: HEALTH_COLOR.degraded, label: "Degraded (load over 85%)" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle (low, dark)" },
  { glyph: "spark", color: "var(--foreground)", label: "Run started (a still pip when reduced)" },
  { glyph: "ring", color: HEALTH_COLOR.degraded, label: "Cluster degraded (platform edge)" },
];

/** Pixels per world unit; the viewBox scales the whole picture to the container. */
const UNIT = 22;
const TWEEN_MS = 520;
/** A spark lives as long as its rise animation (viz-rise is 1.4s). */
const SPARK_MS = 1500;
/** Reduced motion keeps a start pip up a little longer, since nothing moves. */
const PIP_MS = 3000;
/** Width of one mono label character at the label size, for truncation. */
const LABEL_CHAR_PX = 6.6;

const CLUSTER_HEALTH: Record<FleetCluster["health"], Health> = {
  ok: "ok",
  degraded: "degraded",
  offline: "failing",
};

/* ---- one shared tween loop ------------------------------------------- */

interface Tween {
  from: number;
  to: number;
  start: number;
  apply: (h: number) => void;
}

function createAnimator() {
  const tweens = new Map<string, Tween>();
  let frame = 0;
  const hasRaf =
    typeof window !== "undefined" && typeof window.requestAnimationFrame === "function";
  const tick = (t: number) => {
    for (const [id, tw] of tweens) {
      const p = Math.min(1, Math.max(0, (t - tw.start) / TWEEN_MS));
      const e = 1 - Math.pow(1 - p, 3);
      tw.apply(tw.from + (tw.to - tw.from) * e);
      if (p >= 1) tweens.delete(id);
    }
    frame = tweens.size ? window.requestAnimationFrame(tick) : 0;
  };
  return {
    start(id: string, from: number, to: number, apply: (h: number) => void) {
      if (!hasRaf) {
        apply(to);
        return;
      }
      tweens.set(id, { from, to, start: performance.now(), apply });
      if (!frame) frame = window.requestAnimationFrame(tick);
    },
    stop(id: string) {
      tweens.delete(id);
    },
    cancel() {
      tweens.clear();
      if (frame && hasRaf) window.cancelAnimationFrame(frame);
      frame = 0;
    },
  };
}

type Animator = ReturnType<typeof createAnimator>;

/* ---- colours ---------------------------------------------------------- */

function topColor(health: Health): string {
  // Idle is the muted tone pushed most of the way into the card: dark, unlit.
  return health === "idle"
    ? "color-mix(in oklab, var(--muted-foreground) 32%, var(--card))"
    : HEALTH_COLOR[health];
}

const PLATFORM_TOP = "color-mix(in oklab, var(--card) 86%, var(--foreground))";
const SHADOW = "color-mix(in oklab, black 55%, transparent)";

/* ---- a box ------------------------------------------------------------ */

interface AgentBoxProps {
  id: string;
  name: string;
  health: Health;
  load: number;
  activeRuns: number;
  x: number;
  y: number;
  height: number;
  selected: boolean;
  reduced: boolean;
  animator: Animator;
  onSelect?: (id: string) => void;
  onPeek: (id: string | null) => void;
}

const AgentBox = React.memo(function AgentBox({
  id,
  name,
  health,
  load,
  activeRuns,
  x,
  y,
  height,
  selected,
  reduced,
  animator,
  onSelect,
  onPeek,
}: AgentBoxProps) {
  const topRef = React.useRef<SVGPolygonElement>(null);
  const leftRef = React.useRef<SVGPolygonElement>(null);
  const rightRef = React.useRef<SVGPolygonElement>(null);
  const haloRef = React.useRef<SVGPolygonElement>(null);
  const ringRef = React.useRef<SVGPolygonElement>(null);
  const shown = React.useRef<number | null>(null);

  // React commits the target geometry; before paint this rewinds it to the
  // height on screen and tweens from there, so only changed boxes move.
  React.useLayoutEffect(() => {
    const apply = (h: number) => {
      shown.current = h;
      const f = isoBox(x, y, BOX, BOX, h, UNIT);
      topRef.current?.setAttribute("points", f.top);
      leftRef.current?.setAttribute("points", f.left);
      rightRef.current?.setAttribute("points", f.right);
      ringRef.current?.setAttribute("points", f.top);
      haloRef.current?.setAttribute("points", haloPoints(x, y, h));
    };
    const from = shown.current;
    if (from === null) {
      shown.current = height;
      return;
    }
    if (reduced || Math.abs(from - height) < 0.005) {
      animator.stop(id);
      apply(height);
      return;
    }
    apply(from);
    animator.start(id, from, height, apply);
  }, [animator, height, id, reduced, x, y]);

  React.useEffect(() => () => animator.stop(id), [animator, id]);

  const faces = isoBox(x, y, BOX, BOX, height, UNIT);
  const color = topColor(health);
  const failing = health === "failing";
  const label = `${name}: ${HEALTH_LABEL[health]}, load ${Math.round(load * 100)}%, ${activeRuns} active run${activeRuns === 1 ? "" : "s"}`;

  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={label}
      aria-pressed={selected}
      className="group cursor-pointer outline-none"
      onClick={() => onSelect?.(id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(id);
        }
      }}
      onPointerEnter={() => onPeek(id)}
      onPointerLeave={() => onPeek(null)}
      onFocus={() => onPeek(id)}
      onBlur={() => onPeek(null)}
    >
      <title>{label}</title>
      {/* Contact shadow on the platform, cast down-right. */}
      <polygon points={shadowPoints(x, y)} fill={SHADOW} />
      <g className={cn(failing && MOTION_CLASS.flicker)}>
        {HEALTH_GLOWS[health] && (
          <polygon
            ref={haloRef}
            points={haloPoints(x, y, height)}
            fill={`color-mix(in oklab, ${color} 24%, transparent)`}
            stroke={`color-mix(in oklab, ${color} 12%, transparent)`}
            strokeWidth={5}
            strokeLinejoin="round"
          />
        )}
        <polygon ref={leftRef} points={faces.left} fill={shade(color, 0.45)} />
        <polygon ref={rightRef} points={faces.right} fill={shade(color, 0.62)} />
        <polygon
          ref={topRef}
          points={faces.top}
          fill={color}
          stroke={
            failing
              ? HEALTH_COLOR.failing
              : "color-mix(in oklab, var(--foreground) 22%, transparent)"
          }
          strokeWidth={failing ? 1.2 : 0.5}
        />
      </g>
      <polygon
        ref={ringRef}
        points={faces.top}
        fill="none"
        stroke="var(--ring)"
        strokeWidth={2}
        strokeLinejoin="round"
        className={cn(
          "pointer-events-none transition-opacity",
          selected
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-60 group-focus-visible:opacity-100"
        )}
      />
    </g>
  );
});

function haloPoints(x: number, y: number, h: number): string {
  const grow = 0.16;
  return points([
    iso(x - grow, y - grow, h, UNIT),
    iso(x + BOX + grow, y - grow, h, UNIT),
    iso(x + BOX + grow, y + BOX + grow, h, UNIT),
    iso(x - grow, y + BOX + grow, h, UNIT),
  ]);
}

function shadowPoints(x: number, y: number): string {
  const s = 0.14;
  return points([
    iso(x + s, y - 0.02, 0, UNIT),
    iso(x + BOX + s * 2, y - 0.02, 0, UNIT),
    iso(x + BOX + s * 2, y + BOX + s, 0, UNIT),
    iso(x + s, y + BOX + s, 0, UNIT),
  ]);
}

/* ---- the view --------------------------------------------------------- */

export function FleetIsometric({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const reduced = motion === "reduced";
  const [animator] = React.useState(createAnimator);
  React.useEffect(() => () => animator.cancel(), [animator]);

  const [peekId, setPeekId] = React.useState<string | null>(null);

  // Layout depends on which agents sit where, not on their load, so it only
  // recomputes when membership changes.
  const membership = snapshot.agents.map((a) => `${a.id}@${a.clusterId}`).join(",");
  const clusterKey = snapshot.clusters.map((c) => c.id).join(",");
  const platforms = React.useMemo(
    () => layoutFleet(snapshot),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on membership, see above
    [membership, clusterKey]
  );
  const bounds = React.useMemo(() => fleetBounds(platforms, UNIT), [platforms]);

  const agentsById = new Map(snapshot.agents.map((a) => [a.id, a]));
  const clustersById = new Map(snapshot.clusters.map((c) => [c.id, c]));
  const boxPos = new Map(platforms.flatMap((p) => p.boxes.map((b) => [b.agent.id, b] as const)));
  const starts = recentStarts(snapshot, reduced ? PIP_MS : SPARK_MS);
  const summary = summarizeFleet(snapshot);
  const peek = peekId ? agentsById.get(peekId) : undefined;
  const peekCluster = peek ? clustersById.get(peek.clusterId) : undefined;

  return (
    <div
      data-motion={motion}
      role="group"
      aria-label={fleetAriaLabel(summary)}
      className={cn("relative h-full w-full", className)}
    >
      <svg
        viewBox={`${bounds.minX.toFixed(1)} ${bounds.minY.toFixed(1)} ${bounds.width.toFixed(1)} ${bounds.height.toFixed(1)}`}
        preserveAspectRatio="xMidYMid meet"
        className="block h-full max-h-[70vh] w-full"
      >
        <g>
          {platforms.map((p) => {
            const cluster = clustersById.get(p.cluster.id) ?? p.cluster;
            return (
              <g key={p.cluster.id}>
                <Platform x={p.x} y={p.y} w={p.w} d={p.d} health={cluster.health} />
                {p.boxes.map((b) => {
                  const a = agentsById.get(b.agent.id) ?? b.agent;
                  return (
                    <AgentBox
                      key={a.id}
                      id={a.id}
                      name={a.name}
                      health={a.health}
                      load={a.load}
                      activeRuns={a.activeRuns}
                      x={b.x}
                      y={b.y}
                      height={boxHeight(a)}
                      selected={a.id === selectedAgentId}
                      reduced={reduced}
                      animator={animator}
                      onSelect={onSelectAgent}
                      onPeek={setPeekId}
                    />
                  );
                })}
              </g>
            );
          })}

          {/* Run starts, on top so a nearer tall box never hides one. */}
          <g className="pointer-events-none" aria-hidden>
            {starts.map((s) => {
              const b = boxPos.get(s.agentId);
              const a = agentsById.get(s.agentId);
              if (!b || !a) return null;
              const [sx, sy] = iso(b.x + BOX / 2, b.y + BOX / 2, boxHeight(a), UNIT);
              return (
                <g key={s.id} transform={`translate(${sx.toFixed(1)} ${sy.toFixed(1)})`}>
                  {reduced ? (
                    <circle r={2.2} fill="var(--foreground)" />
                  ) : (
                    <g className={MOTION_CLASS.rise}>
                      <line
                        y1={0}
                        y2={-10}
                        stroke="var(--foreground)"
                        strokeWidth={1.2}
                        strokeLinecap="round"
                        opacity={0.7}
                      />
                      <circle cy={-11} r={1.8} fill="var(--foreground)" />
                    </g>
                  )}
                </g>
              );
            })}
          </g>

          {/* Cluster labels run along each platform's front-left edge, in the
           * gap before the next platform, haloed so a tall box never hides one. */}
          {platforms.map((p) => {
            const cluster = clustersById.get(p.cluster.id) ?? p.cluster;
            const [lx, ly] = iso(p.x + 0.2, p.y + p.d, -SLAB, UNIT);
            const chars = Math.max(4, Math.floor((p.w * UNIT - 20) / LABEL_CHAR_PX));
            const name = truncate(cluster.name, chars);
            return (
              <g
                key={`label-${p.cluster.id}`}
                transform={`translate(${lx.toFixed(1)} ${ly.toFixed(1)}) rotate(30)`}
              >
                <title>{`${cluster.name}${cluster.region ? ` (${cluster.region})` : ""}: ${p.boxes.length} agents`}</title>
                <circle
                  cx={3}
                  cy={11}
                  r={2.6}
                  fill={HEALTH_COLOR[CLUSTER_HEALTH[cluster.health]]}
                />
                <text
                  x={10}
                  y={14.5}
                  className="fill-foreground font-mono"
                  fontSize={10.5}
                  letterSpacing={0.4}
                  stroke="var(--card)"
                  strokeWidth={3}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                >
                  {name}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {peek && (
        <div
          aria-hidden
          className="bg-card/90 pointer-events-none absolute top-2 left-2 max-w-[60%] rounded-sm border px-2 py-1 font-mono text-xs shadow-sm backdrop-blur-sm"
        >
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block size-2 shrink-0 rounded-full"
              style={{ background: topColor(peek.health) }}
            />
            <span className="truncate font-semibold" title={peek.name}>
              {peek.name}
            </span>
          </div>
          <div className="text-muted-foreground truncate">
            {HEALTH_LABEL[peek.health]} · load {Math.round(peek.load * 100)}% · {peek.activeRuns}{" "}
            running · {peek.queued} queued{peekCluster ? ` · ${peekCluster.name}` : ""}
          </div>
        </div>
      )}
    </div>
  );
}

function Platform({
  x,
  y,
  w,
  d,
  health,
}: {
  x: number;
  y: number;
  w: number;
  d: number;
  health: FleetCluster["health"];
}) {
  const f = isoBox(x, y, w, d, SLAB, UNIT, -SLAB);
  // The two front edges of the top face catch the light; a degraded or
  // offline cluster tints them with its status so the slab itself reports.
  const edge =
    health === "ok"
      ? "color-mix(in oklab, var(--foreground) 22%, transparent)"
      : HEALTH_COLOR[CLUSTER_HEALTH[health]];
  const front = points([
    iso(x, y + d, 0, UNIT),
    iso(x + w, y + d, 0, UNIT),
    iso(x + w, y, 0, UNIT),
  ]);
  return (
    <g aria-hidden>
      <polygon points={f.left} fill={shade(PLATFORM_TOP, 0.35)} />
      <polygon points={f.right} fill={shade(PLATFORM_TOP, 0.55)} />
      <polygon points={f.top} fill={PLATFORM_TOP} stroke="var(--border)" strokeWidth={0.6} />
      <polyline
        points={front}
        fill="none"
        stroke={edge}
        strokeWidth={health === "ok" ? 0.9 : 1.4}
        strokeLinejoin="round"
      />
    </g>
  );
}
