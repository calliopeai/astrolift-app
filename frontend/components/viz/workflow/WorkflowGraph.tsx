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
import { WORKFLOW_SHAPE_LEGEND, type LegendItem } from "../core/VizLegend";
import type {
  BranchState,
  GateState,
  WorkflowSnapshot,
  WorkflowViewProps,
} from "../core/workflow-model";
import {
  FLOW_MIN,
  GATE_R,
  LABEL_W,
  describeWorkflowGraph,
  layoutWorkflowGraph,
  pointAlong,
  truncate,
  type FanoutBox,
  type GraphEdge,
  type GraphNode,
  type LoopRun,
  type NestedGroup,
  type Point,
  type ReturnTrack,
  type SupervisorSwarm,
  type TrainGroup,
} from "./workflow-graph-layout";

/**
 * Workflows as an animated stage graph (spec 44 viz addendum, the Graph
 * option). One lane per workflow; the forward flow runs left to right, stages
 * are nodes, gates are diamonds with a signal, segments are edges. Motion is
 * state: dashes flow at the segment's throughput, particles (a preference)
 * ride busy edges, a backed-up edge turns amber and thickens, held runs
 * breathe at their gate, a failed run flickers.
 *
 * The shapes: a loop's return track runs dashed under the lane (a retry is a
 * siding under its stage), labelled with its bound, and it flows only while a
 * run is being sent back along it, with that run drawn where it is on the
 * track. A fanout is a box of its branches; a running branch's feed flows and
 * a branch that settles flashes once. A supervisor's workers are satellites
 * that circle it as fast as they are busy (idle workers hold still). A nested
 * workflow is a group holding its child line, collapsible to a stack.
 *
 * Reduced motion draws the same picture still: flow dashes stop but keep their
 * throughput-scaled opacity, particles are left out, satellites rest in place,
 * held and failed badges keep their colour and glyph, and every backlog,
 * bound and count is text.
 */

export const WORKFLOW_GRAPH_LEGEND: LegendItem[] = [
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Runs flowing, faster when busier" },
  { glyph: "flow", color: HEALTH_COLOR.degraded, label: "Backed up (more than 3 queued)" },
  { glyph: "signal", color: HEALTH_COLOR.degraded, label: "Gate waiting on approval" },
  { glyph: "signal", color: HEALTH_COLOR.ok, label: "Gate approved" },
  { glyph: "signal", color: HEALTH_COLOR.failing, label: "Gate denied or rejected" },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Runs at a stage" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failed run" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle or finished" },
  {
    ...WORKFLOW_SHAPE_LEGEND.returnTrack,
    label:
      "Return track: a loop back to an earlier stage, with its round bound; flows while a run is sent back",
  },
  WORKFLOW_SHAPE_LEGEND.siding,
  WORKFLOW_SHAPE_LEGEND.roundBadge,
  WORKFLOW_SHAPE_LEGEND.roundNearBound,
  {
    glyph: "bar",
    color: HEALTH_COLOR.ok,
    label:
      "Fan-out box: one row per branch; a running branch's feed flows, a settled one flashes once",
  },
  WORKFLOW_SHAPE_LEGEND.join,
  {
    ...WORKFLOW_SHAPE_LEGEND.supervisorSwarm,
    label:
      "Supervisor satellites: its workers, lit and circling while busy, faster under more load",
  },
  {
    ...WORKFLOW_SHAPE_LEGEND.nestedStack,
    label: "Nested group: a stage that runs a child workflow; collapse it to a stack",
  },
];

const GATE_HEALTH: Record<GateState, Health> = {
  waiting: "degraded",
  approved: "ok",
  denied: "failing",
};

const BRANCH_HEALTH: Record<BranchState, Health> = {
  queued: "idle",
  running: "ok",
  succeeded: "ok",
  failed: "failing",
};

/** Return tracks move work at the model's loop speed, a track per two seconds. */
const TRACK_SPEED = 0.5;

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

const pts = (points: Point[]) => points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");

/** A particle riding a polyline, moved by the one animation loop. */
function Particle({
  points,
  speed,
  phase,
  color,
  health,
}: {
  points: Point[];
  speed: number;
  phase: number;
  color: string;
  health: Health;
}) {
  const p = pointAlong(points, phase);
  return (
    <circle
      data-particle=""
      data-pts={pts(points)}
      data-speed={speed}
      data-phase={phase}
      cx={p.x}
      cy={p.y}
      r={2}
      fill={color}
      style={glow(health)}
    />
  );
}

function Edge({ edge, particles }: { edge: GraphEdge; particles: boolean }) {
  const color = HEALTH_COLOR[edge.health];
  const width = edge.backedUp ? 3.5 : 1.5;
  const n = edge.flowing ? 1 + Math.round(edge.rate * 2) : 0;
  const speed = 0.15 + edge.rate * 0.6;
  const mid = (edge.x1 + edge.x2) / 2;
  const line = [
    { x: edge.x1, y: edge.y },
    { x: edge.x2, y: edge.y },
  ];
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
        Array.from({ length: n }, (_, i) => (
          <Particle
            key={i}
            points={line}
            speed={speed}
            phase={i / n}
            color={color}
            health={edge.health}
          />
        ))}
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

/** A loop's way back: dashed and still, flowing only while a run travels it. */
function Track({ track, particles }: { track: ReturnTrack; particles: boolean }) {
  const color = HEALTH_COLOR.degraded;
  const end = track.points[track.points.length - 1];
  const d = pts(track.points);
  const labelW = track.short.length * 5.4 + 6;
  return (
    <g aria-hidden>
      <title>{track.title}</title>
      <polyline
        points={d}
        fill="none"
        stroke={track.active ? color : tint(color, 55)}
        strokeWidth={1.25}
        strokeDasharray="4 3"
        strokeLinejoin="round"
      />
      {track.active && (
        <polyline
          points={d}
          fill="none"
          className={MOTION_CLASS.flow}
          stroke={color}
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ ["--viz-flow-duration" as string]: flowDuration(0.6) }}
        />
      )}
      <path
        d={`M${end.x - 3} ${end.y + 5} L${end.x} ${end.y} L${end.x + 3} ${end.y + 5}`}
        fill="none"
        stroke={color}
        strokeWidth={1.25}
      />
      {particles &&
        track.active &&
        [0, 0.5].map((phase) => (
          <Particle
            key={phase}
            points={track.points}
            speed={TRACK_SPEED}
            phase={phase}
            color={color}
            health="degraded"
          />
        ))}
      {/* A solid backing, so the dashed track never shows between the words. */}
      <rect
        x={track.labelAnchor === "start" ? track.labelX - 3 : track.labelX - labelW / 2}
        y={track.labelY - 6}
        width={labelW}
        height={11}
        rx={2}
        fill="var(--card)"
      />
      <text
        x={track.labelX}
        y={track.labelY + 3}
        textAnchor={track.labelAnchor}
        fontSize={9}
        className="font-mono"
        fill={track.active ? color : "var(--muted-foreground)"}
      >
        {track.short}
      </text>
    </g>
  );
}

function RectNode({ node, fill, color }: { node: GraphNode; fill: string; color: string }) {
  const { x, y, w, h } = node;
  const flicker = node.health === "failing" ? MOTION_CLASS.flicker : undefined;
  const kind = node.station.kind;
  const nested = kind === "workflow";
  const join = kind === "join";
  const inset = join ? 8 : 0;
  return (
    <>
      <rect
        x={x - w / 2 - 3}
        y={y - h / 2 - 3}
        width={w + 6}
        height={h + 6}
        rx={2}
        fill="none"
        stroke="var(--brand-primary)"
        strokeWidth={1.5}
        className={RING}
      />
      {nested && (
        // The stack: a card behind, so the node reads as holding a workflow.
        <rect
          x={x - w / 2 + 4}
          y={y - h / 2 - 4}
          width={w}
          height={h}
          rx={2}
          fill="var(--card)"
          stroke="var(--border)"
        />
      )}
      <rect
        x={x - w / 2}
        y={y - h / 2}
        width={w}
        height={h}
        rx={2}
        fill={fill}
        stroke={node.health === "idle" ? "var(--border)" : color}
        strokeWidth={kind === "supervisor" ? 2 : 1.25}
        className={flicker}
        style={glow(node.health)}
      />
      {join && (
        <path
          d={`M${x - w / 2 + 4} ${y - 6} L${x - w / 2 + 12} ${y} M${x - w / 2 + 4} ${y} H${x - w / 2 + 12} M${x - w / 2 + 4} ${y + 6} L${x - w / 2 + 12} ${y}`}
          stroke={node.health === "idle" ? "var(--muted-foreground)" : color}
          strokeWidth={1}
        />
      )}
      {kind === "gate" && node.child && (
        <circle
          cx={x - w / 2 + 7}
          cy={y}
          r={3}
          fill={HEALTH_COLOR[node.gate ? GATE_HEALTH[node.gate] : "idle"]}
          className={node.gate === "waiting" ? MOTION_CLASS.breathe : undefined}
        />
      )}
      <text
        x={x + inset / 2}
        y={y + (node.child ? 3.5 : 4)}
        textAnchor="middle"
        fontSize={node.child ? 10 : 11}
        className="font-mono"
        fill={node.health === "idle" ? "var(--muted-foreground)" : "var(--foreground)"}
      >
        {truncate(node.station.name, node.child ? 11 : join ? 11 : 13)}
      </text>
    </>
  );
}

function GateNode({ node, fill, color }: { node: GraphNode; fill: string; color: string }) {
  const flicker = node.health === "failing" ? MOTION_CLASS.flicker : undefined;
  const h: Health = node.gate ? GATE_HEALTH[node.gate] : "idle";
  return (
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
      <circle
        cx={node.x}
        cy={node.y}
        r={4.5}
        fill={HEALTH_COLOR[h]}
        opacity={h === "idle" ? 0.4 : 1}
        className={node.gate === "waiting" ? MOTION_CLASS.breathe : undefined}
        style={glow(h)}
      />
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
  );
}

function BranchGlyph({
  state,
  x,
  y,
  color,
}: {
  state: BranchState;
  x: number;
  y: number;
  color: string;
}) {
  if (state === "succeeded")
    return (
      <path d={`M${x - 2.5} ${y} l2 2 l3.5 -4`} fill="none" stroke={color} strokeWidth={1.25} />
    );
  if (state === "failed")
    return <path d={`M${x - 2.5} ${y - 2.5} l5 5 m0 -5 l-5 5`} stroke={color} strokeWidth={1.25} />;
  if (state === "running") return <circle cx={x} cy={y} r={2} fill={color} />;
  return <circle cx={x} cy={y} r={2} fill="none" stroke={color} />;
}

/** The fanout's group box: header row, a row per branch between a fan-out and a fan-in spine. */
function FanoutNode({
  node,
  box,
  justSettled,
}: {
  node: GraphNode;
  box: FanoutBox;
  justSettled: ReadonlySet<string>;
}) {
  const color = HEALTH_COLOR[node.health];
  const header = node.health === "idle" ? "var(--card)" : tint(color, 14);
  const rows = box.branches;
  const lastY = rows.length ? rows[rows.length - 1].y : box.y + node.h + 10;
  const spineL = box.x + 7;
  const spineR = box.x + box.w - 7;
  const j = box.join;
  return (
    <>
      <rect
        x={box.x - 3}
        y={box.y - 3}
        width={box.w + 6}
        height={box.h + 6}
        rx={3}
        fill="none"
        stroke="var(--brand-primary)"
        strokeWidth={1.5}
        className={RING}
      />
      <rect
        x={box.x}
        y={box.y}
        width={box.w}
        height={box.h}
        rx={3}
        fill={tint("var(--muted-foreground)", 5)}
        stroke={node.health === "idle" ? "var(--border)" : tint(color, 60)}
        strokeDasharray="3 2"
      />
      <rect
        x={box.x}
        y={box.y}
        width={box.w}
        height={node.h}
        rx={2}
        fill={header}
        stroke={node.health === "idle" ? "var(--border)" : color}
        strokeWidth={1.25}
        className={node.health === "failing" ? MOTION_CLASS.flicker : undefined}
        style={glow(node.health)}
      />
      <text
        x={box.x + 8}
        y={node.y + 4}
        fontSize={11}
        className="font-mono"
        fill={node.health === "idle" ? "var(--muted-foreground)" : "var(--foreground)"}
      >
        {truncate(node.station.name, 8)}
      </text>
      <text
        x={box.x + box.w - 8}
        y={node.y + 4}
        textAnchor="end"
        fontSize={10}
        className="font-mono"
        fill={j.failed ? HEALTH_COLOR.failing : "var(--muted-foreground)"}
      >
        {j.total ? `${j.settled}/${j.total}` : "0"}
      </text>
      {rows.length > 0 && (
        <path
          d={`M${spineL} ${box.y + node.h} V${lastY} M${spineR} ${box.y + node.h} V${lastY}`}
          stroke="var(--border)"
          strokeWidth={1}
        />
      )}
      {rows.map((b) => {
        const h = BRANCH_HEALTH[b.state];
        const c = HEALTH_COLOR[h];
        const settled = b.state === "succeeded" || b.state === "failed";
        return (
          <g key={b.id}>
            <title>{`${b.label}: ${b.state}`}</title>
            <line
              x1={spineL}
              x2={b.x}
              y1={b.y}
              y2={b.y}
              stroke={b.state === "running" ? c : "var(--border)"}
              className={b.state === "running" ? MOTION_CLASS.flow : undefined}
              style={
                b.state === "running"
                  ? { ["--viz-flow-duration" as string]: flowDuration(0.5) }
                  : undefined
              }
            />
            <line
              x1={b.x + b.w}
              x2={spineR}
              y1={b.y}
              y2={b.y}
              stroke={settled ? tint(c, 70) : "var(--border)"}
            />
            <rect
              x={b.x}
              y={b.y - 5}
              width={b.w}
              height={10}
              rx={2}
              fill={b.state === "queued" ? "var(--card)" : tint(c, b.state === "running" ? 12 : 22)}
              stroke={b.state === "queued" ? "var(--border)" : c}
              strokeWidth={0.75}
            />
            <BranchGlyph state={b.state} x={b.x + 6} y={b.y} color={c} />
            <text
              x={b.x + 12}
              y={b.y + 3}
              fontSize={8}
              className="font-mono"
              fill={b.state === "queued" ? "var(--muted-foreground)" : "var(--foreground)"}
            >
              {truncate(b.label, 10)}
            </text>
            {justSettled.has(b.id) && (
              // One flash as the branch settles: its outcome just arrived.
              <rect
                x={b.x - 2}
                y={b.y - 7}
                width={b.w + 4}
                height={14}
                rx={3}
                fill="none"
                stroke={c}
                strokeWidth={1.5}
                className={MOTION_CLASS.flash}
                opacity={0}
              />
            )}
          </g>
        );
      })}
      {box.more > 0 && (
        <text
          x={box.x + box.w / 2}
          y={lastY + 12}
          textAnchor="middle"
          fontSize={8}
          className="font-mono"
          fill="var(--muted-foreground)"
        >
          +{box.more} more
        </text>
      )}
      {!rows.length && !box.more && (
        <text
          x={box.x + box.w / 2}
          y={box.y + node.h + 13}
          textAnchor="middle"
          fontSize={8}
          className="font-mono"
          fill="var(--muted-foreground)"
        >
          {box.dynamic ? "branches per run" : "no branches"}
        </text>
      )}
    </>
  );
}

function Station({
  node,
  fan,
  justSettled,
  expanded,
  onSelect,
}: {
  node: GraphNode;
  fan?: FanoutBox;
  justSettled: ReadonlySet<string>;
  expanded?: boolean;
  onSelect?: WorkflowViewProps["onSelectStation"];
}) {
  const color = HEALTH_COLOR[node.health];
  const fill = node.health === "idle" ? "var(--card)" : tint(color, 14);
  const select = () => onSelect?.(node.lineId, node.station.id);
  const isGate = node.station.kind === "gate" && !node.child;
  const showDetail = node.detail && !(node.station.kind === "workflow" && expanded);
  const detailY =
    node.station.kind === "supervisor" ? node.y + node.h / 2 + 38 : node.y + node.h / 2 + 12;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={node.label}
      className={FOCUS}
      onClick={select}
      onKeyDown={onActivate(select)}
    >
      <title>{node.label}</title>
      {fan ? (
        <FanoutNode node={node} box={fan} justSettled={justSettled} />
      ) : isGate ? (
        <GateNode node={node} fill={fill} color={color} />
      ) : (
        <RectNode node={node} fill={fill} color={color} />
      )}
      {showDetail && (
        <text
          x={node.x}
          y={detailY}
          textAnchor="middle"
          fontSize={9}
          className="font-mono"
          fill="var(--muted-foreground)"
        >
          {node.detail}
        </text>
      )}
    </g>
  );
}

/** The supervisor's workers as satellites on a small orbit under it, tethered while busy. */
function Swarm({ swarm, reduced }: { swarm: SupervisorSwarm; reduced: boolean }) {
  return (
    <g aria-hidden>
      <ellipse
        cx={swarm.cx}
        cy={swarm.cy}
        rx={swarm.rx}
        ry={swarm.ry}
        fill="none"
        stroke="var(--border)"
        strokeDasharray="1 3"
      />
      {swarm.workers.map((w) => {
        const x = swarm.cx + Math.cos(w.phase) * swarm.rx;
        const y = swarm.cy + Math.sin(w.phase) * swarm.ry;
        const h: Health = w.busy ? "ok" : "idle";
        const c = HEALTH_COLOR[h];
        return (
          <g key={w.id}>
            <title>{w.label}</title>
            <line
              data-tendril=""
              x1={swarm.anchor.x}
              y1={swarm.anchor.y}
              x2={x}
              y2={y}
              stroke={w.busy ? tint(c, 60) : "transparent"}
              strokeWidth={0.75 + w.load}
            />
            <circle
              data-orbit={reduced || w.speed === 0 ? undefined : ""}
              data-cx={swarm.cx}
              data-cy={swarm.cy}
              data-rx={swarm.rx}
              data-ry={swarm.ry}
              data-phase={w.phase}
              data-speed={w.speed}
              cx={x}
              cy={y}
              r={2.5 + w.load * 1.5}
              fill={c}
              opacity={w.busy ? 1 : 0.45}
              style={w.busy ? glow("ok") : undefined}
            />
          </g>
        );
      })}
    </g>
  );
}

function NestedFrame({
  group,
  onToggle,
}: {
  group: NestedGroup;
  onToggle: (stationId: string) => void;
}) {
  const toggle = () => onToggle(group.stationId);
  const verb = group.expanded ? "Collapse" : "Expand";
  const { x, y } = group.toggle;
  return (
    <>
      {group.expanded && (
        <g aria-hidden>
          <line
            x1={group.anchor.x}
            x2={group.anchor.x}
            y1={group.anchor.y}
            y2={group.y}
            stroke="var(--border)"
            strokeDasharray="2 2"
          />
          <rect
            x={group.x}
            y={group.y}
            width={group.w}
            height={group.h}
            rx={4}
            fill={tint("var(--muted-foreground)", 4)}
            stroke="var(--border)"
            strokeDasharray="4 3"
          />
          <text
            x={group.x + 8}
            y={group.y + 12}
            fontSize={9}
            className="font-mono"
            fill="var(--muted-foreground)"
          >
            {truncate(`${group.childName}, child workflow`, Math.floor((group.w - 16) / 5.4))}
          </text>
        </g>
      )}
      <g
        role="button"
        tabIndex={0}
        aria-expanded={group.expanded}
        aria-label={`${verb} the ${group.childName} child workflow`}
        className={FOCUS}
        onClick={toggle}
        onKeyDown={onActivate(toggle)}
      >
        <title>{`${verb} ${group.childName}`}</title>
        <rect
          x={x - 8}
          y={y - 8}
          width={16}
          height={16}
          rx={2}
          fill="none"
          stroke="var(--brand-primary)"
          strokeWidth={1.5}
          className={RING}
        />
        <rect
          x={x - 6}
          y={y - 6}
          width={12}
          height={12}
          rx={2}
          fill="var(--card)"
          stroke="var(--border)"
        />
        <path
          d={
            group.expanded
              ? `M${x - 3} ${y} H${x + 3}`
              : `M${x - 3} ${y} H${x + 3} M${x} ${y - 3} V${y + 3}`
          }
          stroke="var(--foreground)"
          strokeWidth={1.25}
        />
      </g>
    </>
  );
}

function LoopRunMark({
  run,
  onSelect,
}: {
  run: LoopRun;
  onSelect?: WorkflowViewProps["onSelectRun"];
}) {
  const color = HEALTH_COLOR.degraded;
  const select = () => onSelect?.(run.train.id);
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={run.label}
      className={FOCUS}
      onClick={select}
      onKeyDown={onActivate(select)}
    >
      <title>{run.label}</title>
      <circle
        cx={run.x}
        cy={run.y}
        r={9}
        fill="none"
        stroke="var(--brand-primary)"
        strokeWidth={1.5}
        className={RING}
      />
      <circle
        cx={run.x}
        cy={run.y}
        r={5.5}
        fill={tint(color, 30)}
        stroke={run.near ? HEALTH_COLOR.failing : color}
        strokeWidth={run.near ? 1.75 : 1.25}
        style={glow("degraded")}
      />
      <circle cx={run.x} cy={run.y} r={2} fill={color} />
    </g>
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
  const select = () => onSelect?.(group.trains[0].id);
  const motion =
    group.state === "held"
      ? MOTION_CLASS.breathe
      : group.state === "failed"
        ? MOTION_CLASS.flicker
        : undefined;
  const w = count > 1 ? 26 : 16;
  const round = group.round;
  const roundColor = round?.near ? HEALTH_COLOR.degraded : HEALTH_COLOR.ok;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={group.label}
      className={FOCUS}
      onClick={select}
      onKeyDown={onActivate(select)}
    >
      <title>{group.label}</title>
      <rect
        x={group.x - w / 2 - 3}
        y={group.y - 11}
        width={w + 6 + (round ? 22 : 0)}
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
      {round && (
        <g>
          <rect
            x={group.x + w / 2 + 2}
            y={group.y - 6}
            width={20}
            height={12}
            rx={6}
            fill="var(--card)"
            stroke={roundColor}
          />
          <text
            x={group.x + w / 2 + 12}
            y={group.y + 3}
            textAnchor="middle"
            fontSize={8}
            className="font-mono"
            fill={roundColor}
          >
            {round.short}
          </text>
        </g>
      )}
    </g>
  );
}

/**
 * The one animation loop: particles ride their polylines, satellites circle
 * their supervisor. DOM writes only, no React state.
 */
function useMotionLoop(svg: React.RefObject<SVGSVGElement | null>, active: boolean) {
  React.useEffect(() => {
    if (!active || typeof window.requestAnimationFrame !== "function") return;
    let frame = 0;
    const start = performance.now();
    const paths = new WeakMap<Element, Point[]>();
    const parse = (el: Element, raw: string | undefined) => {
      let p = paths.get(el);
      if (!p) {
        p = (raw ?? "").split(" ").map((xy) => {
          const [x, y] = xy.split(",").map(Number);
          return { x, y };
        });
        paths.set(el, p);
      }
      return p;
    };
    const tick = (now: number) => {
      const t = (now - start) / 1000;
      const root = svg.current;
      root?.querySelectorAll<SVGCircleElement>("[data-particle]").forEach((el) => {
        const d = el.dataset;
        const f = (Number(d.phase) + t * Number(d.speed)) % 1;
        const p = pointAlong(parse(el, d.pts), f);
        el.setAttribute("cx", p.x.toFixed(1));
        el.setAttribute("cy", p.y.toFixed(1));
      });
      root?.querySelectorAll<SVGCircleElement>("[data-orbit]").forEach((el) => {
        const d = el.dataset;
        const a = Number(d.phase) + t * Number(d.speed);
        const x = (Number(d.cx) + Math.cos(a) * Number(d.rx)).toFixed(1);
        const y = (Number(d.cy) + Math.sin(a) * Number(d.ry)).toFixed(1);
        el.setAttribute("cx", x);
        el.setAttribute("cy", y);
        const tendril = el.previousElementSibling;
        if (tendril?.hasAttribute("data-tendril")) {
          tendril.setAttribute("x2", x);
          tendril.setAttribute("y2", y);
        }
      });
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [svg, active]);
}

/** Branches that settled between the previous snapshot and this one. */
function settledSince(prev: WorkflowSnapshot | null, next: WorkflowSnapshot): Set<string> {
  const out = new Set<string>();
  if (!prev) return out;
  const before = new Map<string, BranchState>();
  for (const l of prev.lines)
    for (const s of l.stations)
      if (s.kind === "fanout") for (const b of s.branches) before.set(b.id, b.state);
  for (const l of next.lines)
    for (const s of l.stations)
      if (s.kind === "fanout")
        for (const b of s.branches) {
          const was = before.get(b.id);
          if (was && was !== b.state && (b.state === "succeeded" || b.state === "failed"))
            out.add(b.id);
        }
  return out;
}

export function WorkflowGraph({
  snapshot,
  motion,
  flowParticles = true,
  onSelectRun,
  onSelectStation,
  className,
}: WorkflowViewProps) {
  const [collapsed, setCollapsed] = React.useState<ReadonlySet<string>>(() => new Set());
  const layout = React.useMemo(
    () => layoutWorkflowGraph(snapshot, { collapsed }),
    [snapshot, collapsed]
  );
  // The previous snapshot, kept the way React recommends, to flash a branch once as it settles.
  const [seen, setSeen] = React.useState<{
    snapshot: WorkflowSnapshot;
    prev: WorkflowSnapshot | null;
  }>({ snapshot, prev: null });
  if (seen.snapshot !== snapshot) setSeen({ snapshot, prev: seen.snapshot });
  const justSettled = React.useMemo(
    () => (motion === "full" ? settledSince(seen.prev, snapshot) : new Set<string>()),
    [seen.prev, snapshot, motion]
  );

  const svgRef = React.useRef<SVGSVGElement>(null);
  const reduced = motion === "reduced";
  const particles = flowParticles && !reduced;

  const fans = React.useMemo(
    () => new Map(layout.fanouts.map((f) => [f.stationId, f])),
    [layout.fanouts]
  );
  const expanded = React.useMemo(
    () => new Map(layout.nested.map((n) => [n.stationId, n.expanded])),
    [layout.nested]
  );
  useMotionLoop(svgRef, !reduced && (particles || layout.swarms.length > 0));
  const toggle = React.useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  return (
    <div
      role="group"
      aria-label={describeWorkflowGraph(snapshot)}
      data-motion={motion}
      className={cn("h-full w-full overflow-hidden", className)}
    >
      <svg
        ref={svgRef}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        preserveAspectRatio="xMinYMin meet"
        className="block h-full max-h-[70vh] w-full"
      >
        {layout.lanes.map((lane, i) => (
          <g key={lane.lineId}>
            {i > 0 && (
              <line
                x1={0}
                x2={layout.width}
                y1={lane.top - 2}
                y2={lane.top - 2}
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
        {layout.nested.map((n) => (
          <NestedFrame key={n.stationId} group={n} onToggle={toggle} />
        ))}
        {layout.tracks.map((track) => (
          <Track key={track.id} track={track} particles={particles} />
        ))}
        {layout.edges.map((edge) => (
          <Edge key={edge.id} edge={edge} particles={particles && edge.rate > FLOW_MIN} />
        ))}
        {layout.swarms.map((s) => (
          <Swarm key={s.stationId} swarm={s} reduced={reduced} />
        ))}
        {layout.nodes.map((node) => (
          <Station
            key={node.station.id}
            node={node}
            fan={node.child ? undefined : fans.get(node.station.id)}
            justSettled={justSettled}
            expanded={expanded.get(node.station.id)}
            onSelect={onSelectStation}
          />
        ))}
        {layout.loopRuns.map((run) => (
          <LoopRunMark key={run.id} run={run} onSelect={onSelectRun} />
        ))}
        {layout.groups.map((group) => (
          <RunBadge key={group.id} group={group} onSelect={onSelectRun} />
        ))}
      </svg>
    </div>
  );
}
