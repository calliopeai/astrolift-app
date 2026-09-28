"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { AppSnapshot, AppViewProps } from "../core/app-model";
import {
  HEALTH_COLOR,
  HEALTH_GLOWS,
  MOTION_CLASS,
  flowDuration,
  type Health,
} from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import {
  G_NODE_H,
  G_NODE_W,
  ROLE_TAG,
  describeApp,
  encodePts,
  layoutAppGraph,
  nodeLabel,
  particleCount,
  recency,
  recentEvents,
  truncate,
  type GraphEdgePath,
  type GraphNodeBox,
} from "./app-render-layout";
import { useEdgeParticles } from "./use-edge-particles";

/**
 * The app as an animated layered graph (spec 44 viz addendum, the Graph
 * option). Left to right: what calls in (ingress, triggers, schedules), what
 * runs (services, workers, functions, agents), what it keeps or calls out to
 * (data, queues, externals). Motion is state: dashes flow at the edge's
 * request rate and turn red over 5% errors, particles (a preference) ride busy
 * edges, a node's ring is its health and its bar its load, a function lights
 * when invoked and fades over the event window, an agent's beacon blinks on
 * each action, and a failing node flickers.
 *
 * Reduced motion draws the same picture still: dashes stop but keep their
 * rate-scaled opacity, particles are left out, and invoke and action marks
 * are drawn at the brightness their age gives, with the count beside them.
 */

export const APP_GRAPH_LEGEND: LegendItem[] = [
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Traffic, faster and brighter when busier" },
  { glyph: "flow", color: HEALTH_COLOR.failing, label: "Over 5% of calls failing" },
  { glyph: "ring", color: HEALTH_COLOR.ok, label: "Healthy" },
  { glyph: "ring", color: HEALTH_COLOR.degraded, label: "Degraded or replicas short" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing" },
  { glyph: "bar", color: HEALTH_COLOR.ok, label: "Load" },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Function invoked, fades over 12 s" },
  { glyph: "signal", color: HEALTH_COLOR.ok, label: "Agent acted, beacon fades over 12 s" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle" },
];

const tint = (color: string, pct: number) => `color-mix(in oklab, ${color} ${pct}%, var(--card))`;

/** Glow follows HEALTH_GLOWS, the one rule every viz style shares. */
const alarm = (h: Health): React.CSSProperties | undefined =>
  HEALTH_GLOWS[h]
    ? { filter: `drop-shadow(0 0 4px color-mix(in oklab, ${HEALTH_COLOR[h]} 55%, transparent))` }
    : undefined;

function onActivate(fn: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

/** Above this many edges only erroring edges are labelled. */
const RATE_LABEL_MAX_EDGES = 6;

const FOCUS = "group cursor-pointer outline-none";
const RING = "opacity-0 group-focus-visible:opacity-100";

function fmtRps(rps: number): string {
  return rps >= 1000 ? `${(rps / 1000).toFixed(1)}k/s` : `${Math.round(rps)}/s`;
}

function Edge({
  path,
  particles,
  showRate,
}: {
  path: GraphEdgePath;
  particles: boolean;
  showRate: boolean;
}) {
  const color = HEALTH_COLOR[path.health];
  const failing = path.health === "failing";
  const width = failing ? 2.5 : 1.5;
  const n = particles ? particleCount(path.rate) : 0;
  const pts = encodePts(path.pts);
  const title = `${path.edge.from} to ${path.edge.to}: ${fmtRps(path.edge.rps)}, ${(path.edge.errorRate * 100).toFixed(1)}% errors`;
  return (
    <g>
      <title>{title}</title>
      <path
        d={path.d}
        fill="none"
        stroke={failing ? tint(color, 55) : "var(--border)"}
        strokeWidth={width}
      />
      {path.rate > 0 && (
        <path
          d={path.d}
          fill="none"
          className={MOTION_CLASS.flow}
          stroke={color}
          strokeWidth={width}
          strokeLinecap="round"
          opacity={0.35 + path.rate * 0.65}
          style={{ ["--viz-flow-duration" as string]: flowDuration(path.rate) }}
        />
      )}
      {Array.from({ length: n }, (_, i) => (
        <circle
          key={i}
          data-particle=""
          data-pts={pts}
          data-speed={40 + path.rate * 120}
          data-phase={i / n}
          cx={path.pts[0][0]}
          cy={path.pts[0][1]}
          r={2.2}
          fill={color}
        />
      ))}
      {(failing || showRate) && (
        <text
          x={path.mid[0]}
          y={path.mid[1] - 5}
          textAnchor="middle"
          fontSize={9}
          className="font-mono"
          fill={failing ? color : "var(--muted-foreground)"}
        >
          {failing ? `${Math.round(path.edge.errorRate * 100)}% err` : fmtRps(path.edge.rps)}
        </text>
      )}
    </g>
  );
}

function Node({
  box,
  snapshot,
  onSelect,
}: {
  box: GraphNodeBox;
  snapshot: AppSnapshot;
  onSelect?: AppViewProps["onSelectNode"];
}) {
  const { node, x, y } = box;
  const short = node.replicas && node.replicas.ready < node.replicas.desired;
  // Replicas short of desired reads as degraded even if the model says ok.
  const h: Health = node.health === "ok" && short ? "degraded" : node.health;
  const color = HEALTH_COLOR[h];
  const idle = h === "idle";
  const invokes = node.role === "function" ? recentEvents(snapshot, node.id, "invoked") : [];
  const actions = node.role === "agent" ? recentEvents(snapshot, node.id, "agent_action") : [];
  const lastInvoke = invokes.at(-1);
  const lastAction = actions.at(-1);
  const select = () => onSelect?.(node.id);
  const label = nodeLabel(node, h, invokes.length, actions.length);
  const metric = node.replicas
    ? `${node.replicas.ready}/${node.replicas.desired}`
    : node.rps !== undefined
      ? fmtRps(node.rps)
      : "";
  const barW = (G_NODE_W - 16) * Math.max(0, Math.min(1, node.load));
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={label}
      className={FOCUS}
      onClick={select}
      onKeyDown={onActivate(select)}
    >
      <title>{label}</title>
      <rect
        x={x - 4}
        y={y - 4}
        width={G_NODE_W + 8}
        height={G_NODE_H + 8}
        rx={2}
        fill="none"
        stroke="var(--brand-primary)"
        strokeWidth={1.5}
        className={RING}
      />
      <rect
        x={x}
        y={y}
        width={G_NODE_W}
        height={G_NODE_H}
        rx={2}
        fill={idle ? "var(--card)" : tint(color, 10)}
        stroke={idle ? "var(--border)" : color}
        strokeWidth={2}
        className={h === "failing" ? MOTION_CLASS.flicker : undefined}
        style={alarm(h)}
      />
      {lastInvoke && (
        // Lit on each invoke, fading over the event window; the negative delay
        // starts the fade at the event's real age.
        <rect
          key={lastInvoke.id}
          x={x + 1}
          y={y + 1}
          width={G_NODE_W - 2}
          height={G_NODE_H - 2}
          rx={2}
          fill={tint(color, 45)}
          className={MOTION_CLASS.phosphor}
          opacity={recency(snapshot, lastInvoke)}
          style={{ animationDelay: `-${snapshot.now - lastInvoke.at}ms` }}
        />
      )}
      <text x={x + 8} y={y + 13} fontSize={9} className="font-mono" fill="var(--muted-foreground)">
        {ROLE_TAG[node.role]}
        {invokes.length > 0 && ` ×${invokes.length}`}
        {actions.length > 0 && ` ${actions.length} acts`}
      </text>
      {metric && (
        <text
          x={x + G_NODE_W - 8}
          y={y + 13}
          textAnchor="end"
          fontSize={9}
          className="font-mono"
          fill={short ? HEALTH_COLOR.degraded : "var(--muted-foreground)"}
        >
          {metric}
        </text>
      )}
      <text
        x={x + 8}
        y={y + 30}
        fontSize={11}
        className="font-mono"
        fill={idle ? "var(--muted-foreground)" : "var(--foreground)"}
      >
        {truncate(node.name, 18)}
      </text>
      <rect x={x + 8} y={y + G_NODE_H - 8} width={G_NODE_W - 16} height={3} fill="var(--border)" />
      <rect
        x={x + 8}
        y={y + G_NODE_H - 8}
        width={barW}
        height={3}
        fill={idle ? "var(--muted-foreground)" : color}
      />
      {node.role === "agent" && (
        <g>
          <circle cx={x + G_NODE_W - 10} cy={y + 27} r={3.5} fill="none" stroke="var(--border)" />
          {lastAction && (
            <>
              <circle
                key={`b${lastAction.id}`}
                cx={x + G_NODE_W - 10}
                cy={y + 27}
                r={3.5}
                fill={color}
                className={MOTION_CLASS.phosphor}
                opacity={recency(snapshot, lastAction)}
                style={{ animationDelay: `-${snapshot.now - lastAction.at}ms` }}
              />
              <circle
                key={`r${lastAction.id}`}
                cx={x + G_NODE_W - 10}
                cy={y + 27}
                r={4}
                fill="none"
                stroke={color}
                className={MOTION_CLASS.ripple}
                opacity={0}
                style={{ animationDelay: `-${snapshot.now - lastAction.at}ms` }}
              />
            </>
          )}
        </g>
      )}
    </g>
  );
}

export function AppGraph({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
}: AppViewProps) {
  const layout = React.useMemo(() => layoutAppGraph(snapshot), [snapshot]);
  const svgRef = React.useRef<SVGSVGElement>(null);
  const particles = flowParticles && motion === "full";
  useEdgeParticles(svgRef, particles);

  return (
    <div
      role="group"
      aria-label={describeApp(snapshot)}
      data-motion={motion}
      className={cn("h-full w-full overflow-hidden", className)}
    >
      <svg
        ref={svgRef}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        preserveAspectRatio="xMidYMin meet"
        className="block h-full max-h-[70vh] w-full"
      >
        {layout.edges.map((path) => (
          <Edge
            key={path.id}
            path={path}
            particles={particles}
            // Rate labels crowd a dense graph; there the dash speed carries it.
            showRate={layout.edges.length <= RATE_LABEL_MAX_EDGES}
          />
        ))}
        {layout.nodes.map((box) => (
          <Node key={box.node.id} box={box} snapshot={snapshot} onSelect={onSelectNode} />
        ))}
      </svg>
    </div>
  );
}
