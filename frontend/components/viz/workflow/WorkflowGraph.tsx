"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import {
  HEALTH_COLOR,
  HEALTH_GLOWS,
  MOTION_CLASS,
  flowDuration,
  type Health,
} from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";
import type { GateState, TrainState, WorkflowViewProps } from "../core/workflow-model";
import {
  FLOW_MIN,
  GATE_R,
  LABEL_W,
  NODE_H,
  NODE_W,
  describeWorkflowGraph,
  layoutWorkflowGraph,
  truncate,
  type GraphEdge,
  type GraphNode,
  type TrainGroup,
} from "./workflow-graph-layout";

/**
 * Workflows as an animated stage graph (spec 44 viz addendum, the Graph
 * option). One lane per workflow; stages are nodes, gates are diamonds with a
 * signal, segments are edges. Motion is state: dashes flow at the segment's
 * throughput, particles (a preference) ride busy edges, a backed-up edge turns
 * amber and thickens, held runs breathe at their gate, a failed run flickers.
 *
 * Reduced motion draws the same picture still: flow dashes stop but keep their
 * throughput-scaled opacity, particles are left out, held and failed badges
 * keep their colour and glyph, and every backlog carries its count as text.
 */

export const WORKFLOW_GRAPH_LEGEND: LegendItem[] = [
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Runs flowing, faster when busier" },
  { glyph: "flow", color: HEALTH_COLOR.degraded, label: "Backed up (more than 3 queued)" },
  { glyph: "signal", color: HEALTH_COLOR.degraded, label: "Gate waiting on approval" },
  { glyph: "signal", color: HEALTH_COLOR.ok, label: "Gate approved" },
  { glyph: "signal", color: HEALTH_COLOR.failing, label: "Gate denied" },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Runs at a stage" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failed run" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle or finished" },
];

const GATE_HEALTH: Record<GateState, Health> = {
  waiting: "degraded",
  approved: "ok",
  denied: "failing",
};

const STATE_LABEL: Record<TrainState, string> = {
  moving: "running",
  held: "held",
  failed: "failed",
  done: "finished",
};

const tint = (color: string, pct: number) => `color-mix(in oklab, ${color} ${pct}%, var(--card))`;

const glow = (h: Health): React.CSSProperties | undefined =>
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

const FOCUS = "group cursor-pointer outline-none";
const RING = "opacity-0 group-focus-visible:opacity-100";

function Edge({ edge, particles }: { edge: GraphEdge; particles: boolean }) {
  const color = HEALTH_COLOR[edge.health];
  const width = edge.backedUp ? 3.5 : 1.5;
  const n = edge.flowing ? 1 + Math.round(edge.rate * 2) : 0;
  const speed = 0.15 + edge.rate * 0.6;
  const mid = (edge.x1 + edge.x2) / 2;
  return (
    <g>
      <line
        x1={edge.x1}
        x2={edge.x2}
        y1={edge.y}
        y2={edge.y}
        stroke={edge.backedUp ? tint(color, 60) : "var(--border)"}
        strokeWidth={width}
      />
      {edge.flowing && (
        <line
          x1={edge.x1}
          x2={edge.x2}
          y1={edge.y}
          y2={edge.y}
          className={MOTION_CLASS.flow}
          stroke={color}
          strokeWidth={width}
          strokeLinecap="round"
          opacity={0.35 + edge.rate * 0.65}
          style={{ ["--viz-flow-duration" as string]: flowDuration(edge.rate) }}
        />
      )}
      {particles &&
        Array.from({ length: n }, (_, i) => {
          const phase = i / n;
          return (
            <circle
              key={i}
              data-particle=""
              data-x1={edge.x1}
              data-x2={edge.x2}
              data-speed={speed}
              data-phase={phase}
              cx={edge.x1 + phase * (edge.x2 - edge.x1)}
              cy={edge.y}
              r={2}
              fill={color}
              style={glow(edge.health)}
            />
          );
        })}
      {edge.backlog > 0 && (
        <text
          x={mid}
          y={edge.y + 16}
          textAnchor="middle"
          fontSize={9}
          className="font-mono"
          fill={edge.backedUp ? color : "var(--muted-foreground)"}
        >
          {edge.backlog} queued
        </text>
      )}
    </g>
  );
}

function Station({
  node,
  onSelect,
}: {
  node: GraphNode;
  onSelect?: WorkflowViewProps["onSelectStation"];
}) {
  const color = HEALTH_COLOR[node.health];
  const fill = node.health === "idle" ? "var(--card)" : tint(color, 14);
  const flicker = node.health === "failing" ? MOTION_CLASS.flicker : undefined;
  const select = () => onSelect?.(node.lineId, node.station.id);
  const isGate = node.station.kind === "gate";
  const gateLabel = node.gate ? `, gate ${node.gate}` : "";
  const label = `${node.station.name}${gateLabel}, ${node.health}`;
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
      {isGate ? (
        <>
          <rect
            x={node.x - GATE_R - 4}
            y={node.y - GATE_R - 4}
            width={(GATE_R + 4) * 2}
            height={(GATE_R + 4) * 2}
            rx={2}
            fill="none"
            stroke="var(--brand-primary)"
            strokeWidth={1.5}
            className={RING}
          />
          <polygon
            points={`${node.x},${node.y - GATE_R} ${node.x + GATE_R},${node.y} ${node.x},${node.y + GATE_R} ${node.x - GATE_R},${node.y}`}
            fill={fill}
            stroke={color}
            strokeWidth={1.5}
            strokeLinejoin="round"
            className={flicker}
            style={glow(node.health)}
          />
          <GateSignal node={node} />
          <text
            x={node.x}
            y={node.y + GATE_R + 12}
            textAnchor="middle"
            fontSize={10}
            className="font-mono"
            fill="var(--muted-foreground)"
          >
            {truncate(node.station.name, 14)}
          </text>
        </>
      ) : (
        <>
          <rect
            x={node.x - NODE_W / 2 - 3}
            y={node.y - NODE_H / 2 - 3}
            width={NODE_W + 6}
            height={NODE_H + 6}
            rx={2}
            fill="none"
            stroke="var(--brand-primary)"
            strokeWidth={1.5}
            className={RING}
          />
          <rect
            x={node.x - NODE_W / 2}
            y={node.y - NODE_H / 2}
            width={NODE_W}
            height={NODE_H}
            rx={2}
            fill={fill}
            stroke={node.health === "idle" ? "var(--border)" : color}
            strokeWidth={1.25}
            className={flicker}
            style={glow(node.health)}
          />
          <text
            x={node.x}
            y={node.y + 4}
            textAnchor="middle"
            fontSize={11}
            className="font-mono"
            fill={node.health === "idle" ? "var(--muted-foreground)" : "var(--foreground)"}
          >
            {truncate(node.station.name, 13)}
          </text>
        </>
      )}
    </g>
  );
}

function GateSignal({ node }: { node: GraphNode }) {
  const h: Health = node.gate ? GATE_HEALTH[node.gate] : "idle";
  return (
    <circle
      cx={node.x}
      cy={node.y}
      r={4.5}
      fill={HEALTH_COLOR[h]}
      opacity={h === "idle" ? 0.4 : 1}
      className={node.gate === "waiting" ? MOTION_CLASS.breathe : undefined}
      style={glow(h)}
    />
  );
}

function RunBadge({
  group,
  onSelect,
}: {
  group: TrainGroup;
  onSelect?: WorkflowViewProps["onSelectRun"];
}) {
  const color = HEALTH_COLOR[group.health];
  const count = group.trains.length;
  const labels = group.trains.map((t) => t.label).join(", ");
  const label = `${count} ${STATE_LABEL[group.state]}: ${labels}`;
  const select = () => onSelect?.(group.trains[0].id);
  const motion =
    group.state === "held"
      ? MOTION_CLASS.breathe
      : group.state === "failed"
        ? MOTION_CLASS.flicker
        : undefined;
  const w = count > 1 ? 26 : 16;
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
        x={group.x - w / 2 - 3}
        y={group.y - 11}
        width={w + 6}
        height={22}
        rx={2}
        fill="none"
        stroke="var(--brand-primary)"
        strokeWidth={1.5}
        className={RING}
      />
      <g className={motion} style={glow(group.health)}>
        <rect
          x={group.x - w / 2}
          y={group.y - 8}
          width={w}
          height={16}
          rx={2}
          fill={tint(color, group.health === "idle" ? 20 : 30)}
          stroke={color}
          strokeWidth={1}
        />
        {group.state === "held" ? (
          // A pause mark, so a held run reads as held with motion off too.
          <path
            d={`M${group.x - (count > 1 ? 7 : 2)} ${group.y - 3.5} v7 M${group.x - (count > 1 ? 4 : -1)} ${group.y - 3.5} v7`}
            stroke={color}
            strokeWidth={1.5}
          />
        ) : group.state === "failed" ? (
          <path
            d={`M${group.x - (count > 1 ? 8 : 3)} ${group.y - 3} l6 6 m0 -6 l-6 6`}
            stroke={color}
            strokeWidth={1.5}
          />
        ) : (
          <circle cx={group.x - (count > 1 ? 5.5 : 0)} cy={group.y} r={2.5} fill={color} />
        )}
        {count > 1 && (
          <text
            x={group.x + 6}
            y={group.y + 3.5}
            textAnchor="middle"
            fontSize={10}
            className="font-mono"
            fill="var(--foreground)"
          >
            {count}
          </text>
        )}
      </g>
    </g>
  );
}

/** Move every particle along its edge; one loop, DOM writes only, no React state. */
function useParticles(svg: React.RefObject<SVGSVGElement | null>, active: boolean) {
  React.useEffect(() => {
    if (!active || typeof window.requestAnimationFrame !== "function") return;
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = (now - start) / 1000;
      svg.current?.querySelectorAll<SVGCircleElement>("[data-particle]").forEach((el) => {
        const d = el.dataset;
        const x1 = Number(d.x1);
        const x2 = Number(d.x2);
        const f = (Number(d.phase) + t * Number(d.speed)) % 1;
        el.setAttribute("cx", (x1 + f * (x2 - x1)).toFixed(1));
      });
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [svg, active]);
}

export function WorkflowGraph({
  snapshot,
  motion,
  flowParticles = true,
  onSelectRun,
  onSelectStation,
  className,
}: WorkflowViewProps) {
  const layout = React.useMemo(() => layoutWorkflowGraph(snapshot), [snapshot]);
  const svgRef = React.useRef<SVGSVGElement>(null);
  const particles = flowParticles && motion === "full";
  useParticles(svgRef, particles);

  return (
    <div
      role="group"
      aria-label={describeWorkflowGraph(snapshot)}
      data-motion={motion}
      className={cn("w-full overflow-hidden", className)}
    >
      <svg
        ref={svgRef}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        preserveAspectRatio="xMinYMin meet"
        className="block h-auto w-full"
      >
        {layout.lanes.map((lane, i) => (
          <g key={lane.lineId}>
            {i > 0 && (
              <line
                x1={0}
                x2={layout.width}
                y1={lane.y - 58}
                y2={lane.y - 58}
                stroke="var(--border)"
                strokeDasharray="1 4"
              />
            )}
            <text x={8} y={lane.y + 4} fontSize={11} fontWeight={600} fill="var(--foreground)">
              <title>{lane.name}</title>
              {truncate(lane.name, Math.floor((LABEL_W - 24) / 6.5))}
            </text>
          </g>
        ))}
        {layout.edges.map((edge) => (
          <Edge key={edge.id} edge={edge} particles={particles && edge.rate > FLOW_MIN} />
        ))}
        {layout.nodes.map((node) => (
          <Station key={node.station.id} node={node} onSelect={onSelectStation} />
        ))}
        {layout.groups.map((group) => (
          <RunBadge key={group.id} group={group} onSelect={onSelectRun} />
        ))}
      </svg>
    </div>
  );
}
