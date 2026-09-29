"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { EVENT_WINDOW_MS, type FleetAgent, type FleetViewProps } from "../core/fleet-model";
import {
  flowDuration,
  HEALTH_COLOR,
  HEALTH_GLOWS,
  HEALTH_LABEL,
  MOTION_CLASS,
  type Health,
} from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";
import {
  agentNodeId,
  clusterNodeId,
  createForceSim,
  fleetForceGraph,
  HUB_ID,
  pin,
  reheat,
  release,
  runToRest,
  tick,
  type FleetForceGraph,
  type FleetShape,
  type ForceSim,
} from "./force";

/**
 * Fleet graph (spec 44 viz addendum): the org hub, its clusters and their
 * agents as a live force graph. The layout settles once, animated, on mount
 * and whenever the set of nodes changes, then stops; nothing drifts at rest.
 *
 * What moves, and why:
 * - an edge carrying active runs has a moving dash; busier is faster
 *   (flowDuration of the agent's load, or the cluster's mean load);
 * - each dispatched event sends one pulse hub -> cluster -> agent;
 * - a failing agent flickers; queued runs breathe as a ring;
 * - node size is active runs, colour is health, idle agents stay dark.
 *
 * Reduced motion: the simulation runs to rest synchronously and the picture
 * is drawn still. Dashes stay on busy edges (width still reads load), a
 * recent dispatch rings its agent instead of travelling, failing is the
 * failing colour without the flicker, queued is a still ring.
 *
 * Drag any node to pin it; double-click it to release.
 */

export const FLEET_GRAPH_LEGEND: LegendItem[] = [
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Healthy; size is active runs" },
  { glyph: "glow", color: HEALTH_COLOR.degraded, label: "Degraded (load over 85%)" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle, dark" },
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Runs in progress; faster and wider is busier" },
  {
    glyph: "spark",
    color: "var(--brand-primary)",
    label: "Dispatch travels hub, cluster, agent (still: ringed agent)",
  },
  { glyph: "ring", color: HEALTH_COLOR.idle, label: "Runs queued" },
  { glyph: "signal", color: "var(--muted-foreground)", label: "Pinned; double-click to release" },
];

/** A pulse takes this long to go hub -> cluster -> agent. */
const PULSE_MS = 1100;
/** Only animate dispatches this fresh; older ones already happened. */
const PULSE_FRESH_MS = 3000;
/** Above this many agents, names move into tooltips. */
const LABEL_LIMIT = 40;
const NAME_MAX = 16;

const CLUSTER_HEALTH: Record<"ok" | "degraded" | "offline", Health> = {
  ok: "ok",
  degraded: "degraded",
  offline: "failing",
};

function truncate(name: string, max = NAME_MAX) {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

function agentRadius(a: FleetAgent) {
  return 3.5 + Math.min(a.activeRuns, 4) * 1.4;
}

function glow(color: string, px: number) {
  return `drop-shadow(0 0 ${px}px color-mix(in oklab, ${color} 70%, transparent))`;
}

interface Pulse {
  path: number[];
  start: number;
  el: SVGCircleElement;
}

/**
 * Everything that changes per frame lives here, outside React state: the
 * sim, the DOM nodes it positions, the pulses in flight and the rAF handle.
 */
class GraphEngine {
  private sim: ForceSim | null = null;
  private svg: SVGSVGElement | null = null;
  private pulseLayer: SVGGElement | null = null;
  private nodeEls: [SVGGElement, number][] = [];
  private lineEls: [SVGLineElement, number, number][] = [];
  private pulses: Pulse[] = [];
  private raf: number | null = null;
  private drag: { id: string; x0: number; y0: number; moved: boolean } | null = null;
  private suppressClick = false;

  attach(svg: SVGSVGElement | null) {
    this.svg = svg;
  }

  attachPulseLayer(layer: SVGGElement | null) {
    this.pulseLayer = layer;
  }

  /** A new node set: new sim, carrying positions and pins from the old one. */
  rebuild(graph: FleetForceGraph) {
    this.sim = createForceSim(graph.nodes, graph.links, {
      width: graph.width,
      height: graph.height,
      seed: 7,
      carry: this.sim,
    });
    this.collect();
  }

  collect() {
    const { svg, sim } = this;
    if (!svg || !sim) return;
    this.nodeEls = [];
    svg.querySelectorAll<SVGGElement>("g[data-node]").forEach((el) => {
      const i = sim.index.get(el.dataset.node ?? "");
      if (i !== undefined) this.nodeEls.push([el, i]);
    });
    this.lineEls = [];
    svg.querySelectorAll<SVGLineElement>("line[data-s]").forEach((el) => {
      const s = sim.index.get(el.dataset.s ?? "");
      const t = sim.index.get(el.dataset.t ?? "");
      if (s !== undefined && t !== undefined) this.lineEls.push([el, s, t]);
    });
  }

  paint() {
    const nodes = this.sim?.nodes;
    if (!nodes) return;
    for (const [el, i] of this.nodeEls) {
      el.setAttribute("transform", `translate(${nodes[i].x.toFixed(1)} ${nodes[i].y.toFixed(1)})`);
    }
    for (const [el, s, t] of this.lineEls) {
      el.setAttribute("x1", nodes[s].x.toFixed(1));
      el.setAttribute("y1", nodes[s].y.toFixed(1));
      el.setAttribute("x2", nodes[t].x.toFixed(1));
      el.setAttribute("y2", nodes[t].y.toFixed(1));
    }
  }

  settle(motion: "full" | "reduced") {
    if (!this.sim) return;
    if (motion === "reduced" || typeof window.requestAnimationFrame !== "function") {
      runToRest(this.sim);
      this.paint();
    } else {
      this.start();
    }
  }

  start() {
    if (this.raf !== null) return;
    this.raf = window.requestAnimationFrame(this.frame);
  }

  stop() {
    if (this.raf !== null) window.cancelAnimationFrame(this.raf);
    this.raf = null;
    for (const p of this.pulses) p.el.remove();
    this.pulses = [];
  }

  private frame = () => {
    this.raf = null;
    const sim = this.sim;
    if (!sim) return;
    const moving = tick(sim);
    this.paint();
    this.stepPulses(performance.now());
    if (moving || this.pulses.length > 0) this.start();
  };

  /** Send one pulse hub -> cluster -> agent. */
  addPulse(agentId: string, clusterId: string | undefined) {
    const sim = this.sim;
    if (!sim || !this.pulseLayer) return;
    const path = [HUB_ID, clusterId && clusterNodeId(clusterId), agentNodeId(agentId)]
      .map((id) => (id ? sim.index.get(id) : undefined))
      .filter((i): i is number => i !== undefined);
    if (path.length < 2) return;
    const el = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    el.setAttribute("r", "4");
    el.setAttribute("fill", "var(--brand-primary)");
    el.style.filter = glow("var(--brand-primary)", 5);
    el.style.pointerEvents = "none";
    this.pulseLayer.appendChild(el);
    this.pulses.push({ path, start: performance.now(), el });
    this.start();
  }

  private stepPulses(now: number) {
    const nodes = this.sim?.nodes;
    if (!nodes) return;
    this.pulses = this.pulses.filter((p) => {
      const t = (now - p.start) / PULSE_MS;
      if (t >= 1) {
        p.el.remove();
        return false;
      }
      const legs = p.path.length - 1;
      const leg = Math.min(legs - 1, Math.floor(t * legs));
      const f = t * legs - leg;
      const a = nodes[p.path[leg]];
      const b = nodes[p.path[leg + 1]];
      p.el.setAttribute("cx", (a.x + (b.x - a.x) * f).toFixed(1));
      p.el.setAttribute("cy", (a.y + (b.y - a.y) * f).toFixed(1));
      p.el.setAttribute("opacity", t > 0.85 ? ((1 - t) / 0.15).toFixed(2) : "1");
      return true;
    });
  }

  private toWorld(clientX: number, clientY: number): [number, number] | null {
    const ctm = this.svg?.getScreenCTM?.();
    if (!ctm) return null;
    const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    return [p.x, p.y];
  }

  dragStart(id: string, e: React.PointerEvent<SVGGElement>) {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    this.drag = { id, x0: e.clientX, y0: e.clientY, moved: false };
  }

  dragMove(e: React.PointerEvent<SVGGElement>, motion: "full" | "reduced") {
    const drag = this.drag;
    if (!drag || !this.sim) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 4) return;
    const p = this.toWorld(e.clientX, e.clientY);
    if (!p) return;
    drag.moved = true;
    pin(this.sim, drag.id, p[0], p[1]);
    if (motion === "full") {
      reheat(this.sim, 0.25);
      this.start();
    } else {
      this.paint();
    }
  }

  /** Returns the id pinned by this drag, if it moved. */
  dragEnd(motion: "full" | "reduced"): string | null {
    const drag = this.drag;
    this.drag = null;
    if (!drag?.moved || !this.sim) return null;
    this.suppressClick = true;
    // Still picture: let the neighbours settle around the new pin at once.
    if (motion === "reduced") {
      reheat(this.sim, 0.3);
      this.settle("reduced");
    }
    return drag.id;
  }

  consumeDragClick() {
    const was = this.suppressClick;
    this.suppressClick = false;
    return was;
  }

  unpin(id: string, motion: "full" | "reduced") {
    if (!this.sim) return;
    release(this.sim, id);
    reheat(this.sim, 0.3);
    this.settle(motion);
  }
}

function shapeKey(clusters: { id: string }[], agents: FleetAgent[]): string {
  const known = new Set(clusters.map((c) => c.id));
  const shape: FleetShape = {
    clusters: clusters.map((c) => ({
      id: c.id,
      agentIds: agents.filter((a) => a.clusterId === c.id).map((a) => a.id),
    })),
    orphans: agents.filter((a) => !known.has(a.clusterId)).map((a) => a.id),
  };
  return JSON.stringify(shape);
}

export function FleetGraph({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const { clusters, agents, events, now } = snapshot;
  const [engine] = React.useState(() => new GraphEngine());
  const [pinned, setPinned] = React.useState<ReadonlySet<string>>(() => new Set());
  const seenRef = React.useRef<Set<string>>(new Set());

  const key = React.useMemo(() => shapeKey(clusters, agents), [clusters, agents]);
  const graph = React.useMemo(() => fleetForceGraph(JSON.parse(key) as FleetShape), [key]);
  const { width, height } = graph;
  const unit = width / 900;

  // Build (or rebuild, carrying positions and pins) when the node set changes.
  React.useLayoutEffect(() => {
    engine.rebuild(graph);
    engine.settle(motion);
    return () => engine.stop();
  }, [engine, graph, motion]);

  // Every render may add or drop edge and node elements: re-find them, place them.
  React.useLayoutEffect(() => {
    engine.collect();
    engine.paint();
  });

  // One pulse per dispatched event id, only while it is fresh.
  React.useEffect(() => {
    const seen = seenRef.current;
    const next = new Set<string>();
    const clusterOf = new Map(agents.map((a) => [a.id, a.clusterId]));
    for (const e of events) {
      next.add(e.id);
      if (seen.has(e.id) || e.kind !== "dispatched") continue;
      if (motion === "full" && now - e.at < PULSE_FRESH_MS) {
        engine.addPulse(e.agentId, clusterOf.get(e.agentId));
      }
    }
    seenRef.current = next;
  }, [engine, events, agents, now, motion]);

  const clusterAgents = new Map<string, FleetAgent[]>();
  for (const a of agents) {
    const list = clusterAgents.get(a.clusterId) ?? [];
    list.push(a);
    clusterAgents.set(a.clusterId, list);
  }
  const knownClusters = new Set(clusters.map((c) => c.id));
  const recentDispatch = new Set(
    events
      .filter((e) => e.kind === "dispatched" && now - e.at < EVENT_WINDOW_MS)
      .map((e) => e.agentId)
  );
  const failing = agents.filter((a) => a.health === "failing").length;
  const busy = agents.filter((a) => a.activeRuns > 0).length;
  const queued = agents.reduce((n, a) => n + a.queued, 0);
  const summary =
    `${agents.length} agents across ${clusters.length} clusters, ` +
    `${failing} failing, ${busy} busy, ${queued} runs queued`;
  const showAgentLabels = agents.length <= LABEL_LIMIT;

  const dragProps = (id: string) => ({
    onPointerDown: (e: React.PointerEvent<SVGGElement>) => engine.dragStart(id, e),
    onPointerMove: (e: React.PointerEvent<SVGGElement>) => engine.dragMove(e, motion),
    onPointerUp: () => {
      const moved = engine.dragEnd(motion);
      if (moved) setPinned((prev) => new Set(prev).add(moved));
    },
    onPointerCancel: () => engine.dragEnd(motion),
    onDoubleClick: () => {
      engine.unpin(id, motion);
      setPinned((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    },
  });

  const pinMark = (id: string, r: number) =>
    pinned.has(id) ? (
      <rect
        x={r * 0.6}
        y={-r - 4}
        width={4}
        height={4}
        rx={1}
        fill="var(--muted-foreground)"
        pointerEvents="none"
      />
    ) : null;

  const flowLine = (s: string, t: string, color: string, load: number) => (
    <line
      data-s={s}
      data-t={t}
      className={MOTION_CLASS.flow}
      stroke={`color-mix(in oklab, ${color} 80%, transparent)`}
      strokeWidth={1.2 + load * 1.6}
      strokeLinecap="round"
      vectorEffect="non-scaling-stroke"
      style={{ "--viz-flow-duration": flowDuration(load) } as React.CSSProperties}
    />
  );

  return (
    <div
      role="group"
      aria-label={summary}
      data-motion={motion}
      className={cn("relative w-full", className)}
    >
      <svg
        ref={(el) => {
          engine.attach(el);
        }}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        className="block h-auto w-full touch-none select-none"
      >
        <g>
          {clusters.map((c) => {
            const list = clusterAgents.get(c.id) ?? [];
            const active = list.filter((a) => a.activeRuns > 0);
            const load = active.length ? active.reduce((n, a) => n + a.load, 0) / active.length : 0;
            const id = clusterNodeId(c.id);
            return (
              <g key={id}>
                <line
                  data-s={HUB_ID}
                  data-t={id}
                  stroke="var(--border)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                {active.length > 0 && flowLine(HUB_ID, id, HEALTH_COLOR.ok, load)}
              </g>
            );
          })}
          {agents.map((a) => {
            const parent = knownClusters.has(a.clusterId) ? clusterNodeId(a.clusterId) : HUB_ID;
            const id = agentNodeId(a.id);
            return (
              <g key={id}>
                <line
                  data-s={parent}
                  data-t={id}
                  stroke="var(--border)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                {a.activeRuns > 0 && flowLine(parent, id, HEALTH_COLOR[a.health], a.load)}
              </g>
            );
          })}
        </g>

        <g
          ref={(el) => {
            engine.attachPulseLayer(el);
          }}
        />

        <g>
          <g data-node={HUB_ID} className="cursor-grab" {...dragProps(HUB_ID)}>
            <title>Organization hub</title>
            <circle
              r={16}
              fill="var(--card)"
              stroke="color-mix(in oklab, var(--foreground) 45%, transparent)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
            <text
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={9}
              className="fill-muted-foreground font-mono"
            >
              org
            </text>
            {pinMark(HUB_ID, 16)}
          </g>

          {agents.map((a) => {
            const id = agentNodeId(a.id);
            const r = agentRadius(a);
            const color = HEALTH_COLOR[a.health];
            const idle = a.health === "idle";
            const lit = HEALTH_GLOWS[a.health] && (a.activeRuns > 0 || a.health !== "ok");
            const selected = a.id === selectedAgentId;
            const select = () => onSelectAgent?.(a.id);
            return (
              <g
                key={id}
                data-node={id}
                tabIndex={0}
                aria-label={`${a.name}: ${HEALTH_LABEL[a.health]}, ${a.activeRuns} active, ${a.queued} queued`}
                aria-pressed={selected}
                className="group cursor-pointer outline-none"
                {...dragProps(id)}
                onClick={() => {
                  if (!engine.consumeDragClick()) select();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    select();
                  }
                }}
              >
                <title>{`${a.name}: ${HEALTH_LABEL[a.health]}, ${a.activeRuns} active, ${a.queued} queued, load ${Math.round(a.load * 100)}%`}</title>
                <circle
                  r={r + 6}
                  fill="none"
                  stroke="var(--brand-primary)"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                  className="opacity-0 group-focus-visible:opacity-100"
                />
                {selected && (
                  <circle
                    r={r + 4}
                    fill="none"
                    stroke="var(--foreground)"
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                  />
                )}
                {a.queued > 0 && (
                  <circle
                    r={r + 2.5}
                    fill="none"
                    stroke={HEALTH_COLOR.idle}
                    strokeWidth={1}
                    strokeDasharray="2 2"
                    vectorEffect="non-scaling-stroke"
                    className={MOTION_CLASS.breathe}
                  />
                )}
                {motion === "reduced" && recentDispatch.has(a.id) && (
                  <circle
                    r={r + 2.5}
                    fill="none"
                    stroke="var(--brand-primary)"
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                  />
                )}
                <circle
                  r={r}
                  fill={idle ? "var(--card)" : color}
                  stroke={idle ? color : "none"}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                  className={a.health === "failing" ? MOTION_CLASS.flicker : undefined}
                  style={lit ? { filter: glow(color, 4) } : undefined}
                />
                {(showAgentLabels || selected) && (
                  <text
                    x={r + 4}
                    dominantBaseline="central"
                    fontSize={10 * unit}
                    className="fill-muted-foreground font-mono"
                    stroke="var(--card)"
                    strokeWidth={3 * unit}
                    paintOrder="stroke"
                    pointerEvents="none"
                  >
                    {truncate(a.name)}
                  </text>
                )}
                {pinMark(id, r)}
              </g>
            );
          })}

          {clusters.map((c) => {
            const h = CLUSTER_HEALTH[c.health];
            const color = HEALTH_COLOR[h];
            const id = clusterNodeId(c.id);
            const s = 11;
            return (
              <g key={id} data-node={id} className="cursor-grab" {...dragProps(id)}>
                <title>
                  {`${c.name}${c.region ? ` (${c.region})` : ""}: ${c.health === "offline" ? "Offline" : HEALTH_LABEL[h]}`}
                </title>
                <rect
                  x={-s}
                  y={-s}
                  width={s * 2}
                  height={s * 2}
                  rx={2}
                  fill={`color-mix(in oklab, ${color} 18%, var(--card))`}
                  stroke={color}
                  strokeWidth={1.5}
                  vectorEffect="non-scaling-stroke"
                />
                <text
                  y={s + 12 * unit}
                  textAnchor="middle"
                  fontSize={11 * unit}
                  className="fill-foreground font-mono"
                  stroke="var(--card)"
                  strokeWidth={3 * unit}
                  paintOrder="stroke"
                >
                  {truncate(c.name)}
                </text>
                {pinMark(id, s)}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
