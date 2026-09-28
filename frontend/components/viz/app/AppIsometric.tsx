"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { AppEvent, AppSnapshot, AppViewProps } from "../core/app-model";
import { depth, iso, isoBox, points, shade } from "../core/iso";
import { HEALTH_COLOR, MOTION_CLASS, flowDuration, type Health } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import {
  ISO_UNIT as U,
  MAX_SLABS,
  ROLE_TAG,
  dataHeight,
  describeApp,
  encodePts,
  flashEvent,
  layoutAppIso,
  nodeLabel,
  particleCount,
  recency,
  recentEvents,
  serviceHeight,
  truncate,
  type IsoEdgePath,
  type IsoNodePlace,
} from "./app-render-layout";
import { useEdgeParticles } from "./use-edge-particles";

/**
 * The app as an isometric platform (spec 44 viz addendum, the Isometric
 * option). Each node is a block shaped by its role: a service or worker is a
 * box as tall as its load with one slab per ready replica (missing replicas
 * are dashed outlines); data is a cylinder as tall as its connections in use;
 * a queue is a long low box lit along its length by its backlog; a function is
 * a thin prism that pushes a pulse up when invoked; an agent carries an
 * antenna whose beacon blinks on each action; ingress, triggers, schedules
 * and externals are flat tiles at the platform's rims. Edges are floor paths
 * with dashes flowing at their request rate, red over 5% errors, and
 * particles (a preference) riding busy ones. Failing blocks flicker.
 *
 * Reduced motion draws the same picture still: dashes stop but keep their
 * rate-scaled opacity, particles and pulses are left out, and the lit
 * function tops and agent beacons are drawn at the brightness their age
 * gives, with the invocation or action count beside them.
 */

export const APP_ISOMETRIC_LEGEND: LegendItem[] = [
  { glyph: "bar", color: HEALTH_COLOR.ok, label: "Block height is load" },
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Slab per ready replica" },
  { glyph: "ring", color: HEALTH_COLOR.degraded, label: "Replica missing or degraded" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing" },
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Traffic, faster and brighter when busier" },
  { glyph: "flow", color: HEALTH_COLOR.failing, label: "Over 5% of calls failing" },
  { glyph: "spark", color: HEALTH_COLOR.ok, label: "Function invoked, top fades over 12 s" },
  { glyph: "signal", color: HEALTH_COLOR.ok, label: "Agent acted, beacon fades over 12 s" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle, stays dark" },
];

const SQRT2 = Math.SQRT2;
const COS = Math.cos(Math.PI / 6);

const tint = (color: string, pct: number) => `color-mix(in oklab, ${color} ${pct}%, var(--card))`;

/** Glow is for state worth noticing: failing or degraded, never the resting state. */
const alarm = (h: Health): React.CSSProperties | undefined =>
  h === "failing" || h === "degraded"
    ? { filter: `drop-shadow(0 0 5px color-mix(in oklab, ${HEALTH_COLOR[h]} 55%, transparent))` }
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

/** Top, left and right fills for a block of a health; idle stays dark. */
function faces(h: Health) {
  const top = h === "idle" ? tint(HEALTH_COLOR.idle, 18) : tint(HEALTH_COLOR[h], 38);
  return { top, left: shade(top, 0.45), right: shade(top, 0.25) };
}

function Box({
  x,
  y,
  w,
  d,
  h,
  z = 0,
  health,
}: {
  x: number;
  y: number;
  w: number;
  d: number;
  h: number;
  z?: number;
  health: Health;
}) {
  const f = isoBox(x, y, w, d, h, U, z);
  const c = faces(health);
  const edge = health === "idle" ? "var(--border)" : tint(HEALTH_COLOR[health], 70);
  return (
    <g strokeWidth={0.75} strokeLinejoin="round">
      <polygon points={f.left} fill={c.left} stroke={edge} />
      <polygon points={f.right} fill={c.right} stroke={edge} />
      <polygon points={f.top} fill={c.top} stroke={edge} />
    </g>
  );
}

/** A newest event's fade, started at its real age so a remount does not relight it. */
function fadeStyle(snapshot: AppSnapshot, e: AppEvent): React.CSSProperties {
  return { animationDelay: `-${snapshot.now - e.at}ms` };
}

function Block({
  place,
  health,
  snapshot,
  motion,
}: {
  place: IsoNodePlace;
  health: Health;
  snapshot: AppSnapshot;
  motion: AppViewProps["motion"];
}) {
  const { node, wx, wy, w, d, cx, cy } = place;
  const color = HEALTH_COLOR[health];
  switch (node.role) {
    case "service":
    case "worker": {
      const hb = serviceHeight(node.load);
      const ready = node.replicas?.ready ?? 0;
      const desired = node.replicas?.desired ?? ready;
      const slabs = Math.min(desired, MAX_SLABS);
      return (
        <g>
          <Box x={wx} y={wy} w={w} d={d} h={hb} health={health} />
          {Array.from({ length: slabs }, (_, k) => {
            const z = hb + 0.06 + k * 0.2;
            if (k < ready) {
              return (
                <Box
                  key={k}
                  x={wx + 0.22}
                  y={wy + 0.22}
                  w={w - 0.44}
                  d={d - 0.44}
                  h={0.14}
                  z={z}
                  health={health === "idle" ? "idle" : health === "failing" ? "failing" : "ok"}
                />
              );
            }
            const ghost = isoBox(wx + 0.22, wy + 0.22, w - 0.44, d - 0.44, 0.14, U, z);
            return (
              <polygon
                key={k}
                points={ghost.top}
                fill="none"
                stroke={HEALTH_COLOR.degraded}
                strokeWidth={1}
                strokeDasharray="2 2"
              />
            );
          })}
          {desired > MAX_SLABS && (
            <text
              {...xy(iso(cx, cy, hb + 0.3 + slabs * 0.2, U))}
              textAnchor="middle"
              fontSize={9}
              className="font-mono"
              fill="var(--muted-foreground)"
            >
              {ready}/{desired}
            </text>
          )}
        </g>
      );
    }
    case "data": {
      const hd = dataHeight(node.load);
      const r = 0.65;
      const rx = r * SQRT2 * COS * U;
      const ry = r * SQRT2 * 0.5 * U;
      const [bx, by] = iso(cx, cy, 0, U);
      const ty = by - hd * U;
      const c = faces(health);
      const edge = health === "idle" ? "var(--border)" : tint(color, 70);
      return (
        <g strokeWidth={0.75}>
          <path
            d={`M${bx - rx} ${ty} L${bx - rx} ${by} A${rx} ${ry} 0 0 0 ${bx + rx} ${by} L${bx + rx} ${ty} Z`}
            fill={c.left}
            stroke={edge}
          />
          <ellipse cx={bx} cy={ty} rx={rx} ry={ry} fill={c.top} stroke={edge} />
        </g>
      );
    }
    case "queue": {
      const hq = 0.35;
      const lit = isoBox(wx, wy, w, d * Math.max(0, Math.min(1, node.load)), 0, U, hq);
      return (
        <g>
          <Box x={wx} y={wy} w={w} d={d} h={hq} health={health === "ok" ? "idle" : health} />
          {node.load > 0 && (
            <polygon points={lit.top} fill={tint(color, 60)} stroke="none" opacity={0.9} />
          )}
        </g>
      );
    }
    case "function": {
      const hf = 0.5 + node.load * 0.6;
      const top = isoBox(wx, wy, w, d, hf, U).top;
      const last = recentEvents(snapshot, node.id, "invoked").at(-1);
      const pulse = motion === "full" ? flashEvent(snapshot, node.id, "invoked") : undefined;
      return (
        <g>
          <Box x={wx} y={wy} w={w} d={d} h={hf} health={health} />
          {last && (
            <polygon
              key={`lit${last.id}`}
              points={top}
              fill={HEALTH_COLOR.ok}
              className={MOTION_CLASS.phosphor}
              opacity={recency(snapshot, last)}
              style={fadeStyle(snapshot, last)}
            />
          )}
          {pulse && (
            <polygon
              key={`up${pulse.id}`}
              points={top}
              fill="none"
              stroke={HEALTH_COLOR.ok}
              strokeWidth={1.5}
              className={MOTION_CLASS.rise}
              style={fadeStyle(snapshot, pulse)}
            />
          )}
        </g>
      );
    }
    case "agent": {
      const ha = 0.5 + node.load * 1.2;
      const [ax, ay] = iso(cx, cy, ha, U);
      const [bx, by] = iso(cx, cy, ha + 0.9, U);
      const last = recentEvents(snapshot, node.id, "agent_action").at(-1);
      return (
        <g>
          <Box x={wx} y={wy} w={w} d={d} h={ha} health={health} />
          <line x1={ax} y1={ay} x2={bx} y2={by} stroke="var(--muted-foreground)" strokeWidth={1} />
          <circle cx={bx} cy={by} r={3.5} fill="var(--card)" stroke="var(--border)" />
          {last && (
            <>
              <circle
                key={`b${last.id}`}
                cx={bx}
                cy={by}
                r={3.5}
                fill={HEALTH_COLOR.ok}
                className={MOTION_CLASS.phosphor}
                opacity={recency(snapshot, last)}
                style={fadeStyle(snapshot, last)}
              />
              <circle
                key={`r${last.id}`}
                cx={bx}
                cy={by}
                r={4}
                fill="none"
                stroke={HEALTH_COLOR.ok}
                className={MOTION_CLASS.ripple}
                opacity={0}
                style={fadeStyle(snapshot, last)}
              />
            </>
          )}
        </g>
      );
    }
    default:
      // Ingress, triggers, schedules and externals: flat tiles on the rim.
      return <Box x={wx} y={wy} w={w} d={d} h={0.1} health={health} />;
  }
}

const xy = ([x, y]: [number, number]) => ({ x, y });

/** What a node shows, shared by its block and its caption. */
function nodeView(place: IsoNodePlace, snapshot: AppSnapshot) {
  const { node, wx, wy, w, d, cx, cy } = place;
  const short = !!node.replicas && node.replicas.ready < node.replicas.desired;
  const health: Health = node.health === "ok" && short ? "degraded" : node.health;
  const invokes = node.role === "function" ? recentEvents(snapshot, node.id, "invoked").length : 0;
  const actions =
    node.role === "agent" ? recentEvents(snapshot, node.id, "agent_action").length : 0;
  const metric = node.replicas
    ? `${node.replicas.ready}/${node.replicas.desired}`
    : invokes
      ? `×${invokes}`
      : actions
        ? `${actions} acts`
        : ROLE_TAG[node.role];
  const [, fy] = iso(wx + w, wy + d, 0, U);
  const [lx] = iso(cx, cy, 0, U);
  return { health, short, invokes, actions, metric, fy, lx };
}

function Node({
  place,
  snapshot,
  motion,
  onSelect,
}: {
  place: IsoNodePlace;
  snapshot: AppSnapshot;
  motion: AppViewProps["motion"];
  onSelect?: AppViewProps["onSelectNode"];
}) {
  const { node, wx, wy, w, d } = place;
  const { health, invokes, actions } = nodeView(place, snapshot);
  const label = nodeLabel(node, health, invokes, actions);
  const select = () => onSelect?.(node.id);
  const ring = points([
    iso(wx - 0.25, wy - 0.25, 0, U),
    iso(wx + w + 0.25, wy - 0.25, 0, U),
    iso(wx + w + 0.25, wy + d + 0.25, 0, U),
    iso(wx - 0.25, wy + d + 0.25, 0, U),
  ]);
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
      <polygon
        points={ring}
        fill="none"
        stroke="var(--brand-primary)"
        strokeWidth={1.5}
        className={RING}
      />
      <g className={health === "failing" ? MOTION_CLASS.flicker : undefined} style={alarm(health)}>
        <Block place={place} health={health} snapshot={snapshot} motion={motion} />
      </g>
    </g>
  );
}

/** Name and metric, drawn after every block so no block hides a caption. */
function Caption({ place, snapshot }: { place: IsoNodePlace; snapshot: AppSnapshot }) {
  const { health, short, metric, fy, lx } = nodeView(place, snapshot);
  return (
    <g aria-hidden className="pointer-events-none">
      <text
        x={lx}
        y={fy + 13}
        textAnchor="middle"
        fontSize={10}
        className="font-mono"
        fill={health === "idle" ? "var(--muted-foreground)" : "var(--foreground)"}
        stroke="var(--card)"
        strokeWidth={3}
        paintOrder="stroke"
      >
        {truncate(place.node.name, 14)}
      </text>
      <text
        x={lx}
        y={fy + 24}
        textAnchor="middle"
        fontSize={8.5}
        className="font-mono"
        fill={short ? HEALTH_COLOR.degraded : "var(--muted-foreground)"}
        stroke="var(--card)"
        strokeWidth={3}
        paintOrder="stroke"
      >
        {metric}
      </text>
    </g>
  );
}

function Edge({ path, particles }: { path: IsoEdgePath; particles: boolean }) {
  const color = HEALTH_COLOR[path.health];
  const failing = path.health === "failing";
  const width = failing ? 3 : 2;
  const pts = encodePts(path.pts);
  const n = particles ? particleCount(path.rate) : 0;
  return (
    <g>
      <polyline
        points={pts}
        fill="none"
        stroke={failing ? tint(color, 55) : "var(--border)"}
        strokeWidth={width}
        strokeLinejoin="round"
      />
      {path.rate > 0 && (
        <polyline
          points={pts}
          fill="none"
          className={MOTION_CLASS.flow}
          stroke={color}
          strokeWidth={width}
          strokeLinecap="round"
          strokeLinejoin="round"
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
          r={2.4}
          fill={color}
        />
      ))}
      {failing && (
        <text
          x={path.mid[0]}
          y={path.mid[1] - 6}
          textAnchor="middle"
          fontSize={9}
          className="font-mono"
          fill={color}
        >
          {Math.round(path.edge.errorRate * 100)}% err
        </text>
      )}
    </g>
  );
}

export function AppIsometric({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
}: AppViewProps) {
  const layout = React.useMemo(() => layoutAppIso(snapshot), [snapshot]);
  const svgRef = React.useRef<SVGSVGElement>(null);
  const particles = flowParticles && motion === "full";
  useEdgeParticles(svgRef, particles);
  const { platform: p } = layout;
  const deck = isoBox(p.x0, p.y0, p.x1 - p.x0, p.y1 - p.y0, 0.3, U, -0.3);
  const ordered = [...layout.nodes].sort((a, b) => depth(a.cx, a.cy) - depth(b.cx, b.cy));

  return (
    <div
      role="group"
      aria-label={describeApp(snapshot)}
      data-motion={motion}
      className={cn("w-full overflow-hidden", className)}
    >
      <svg
        ref={svgRef}
        viewBox={layout.viewBox.map((v) => v.toFixed(1)).join(" ")}
        preserveAspectRatio="xMidYMid meet"
        className="block h-auto w-full"
      >
        <g strokeWidth={1} stroke="var(--border)">
          <polygon points={deck.left} fill={shade("var(--card)", 0.35)} />
          <polygon points={deck.right} fill={shade("var(--card)", 0.2)} />
          <polygon points={deck.top} fill="var(--card)" />
        </g>
        {layout.edges.map((path) => (
          <Edge key={path.id} path={path} particles={particles} />
        ))}
        {ordered.map((place) => (
          <Node
            key={place.node.id}
            place={place}
            snapshot={snapshot}
            motion={motion}
            onSelect={onSelectNode}
          />
        ))}
        {ordered.map((place) => (
          <Caption key={place.node.id} place={place} snapshot={snapshot} />
        ))}
      </svg>
    </div>
  );
}
