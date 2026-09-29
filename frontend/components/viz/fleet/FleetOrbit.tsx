"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { EVENT_WINDOW_MS, type FleetViewProps } from "../core/fleet-model";
import {
  HEALTH_COLOR,
  HEALTH_GLOWS,
  HEALTH_LABEL,
  MOTION_CLASS,
  type Health,
} from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import {
  CELL_W,
  DECAY_R,
  LAUNCH_MS,
  PLANET_R,
  RING_R,
  layoutOrbit,
  recentLaunches,
  summarizeFleet,
  type OrbitPlanet,
  type OrbitSatellite,
} from "./orbit-layout";

/**
 * The orbit fleet view, the default (spec 44 viz addendum). Each cluster is a
 * planet and its agents are satellites. Every motion is state: orbit speed is
 * load (idle agents are parked), a failing agent flickers on a low decay
 * orbit, and a run starting sends one launch trail up off the planet.
 *
 * Reduced motion draws the same facts still: satellites evenly spaced on
 * their rings, a danger ring around each failing agent in place of the
 * flicker, and a spark on the planet's limb for each agent that launched in
 * the event window.
 */

export const FLEET_ORBIT_LEGEND: LegendItem[] = [
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Healthy; orbit speed is load" },
  { glyph: "glow", color: HEALTH_COLOR.degraded, label: "Degraded, over 85% load" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing, sunk to a low orbit" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle, parked" },
  { glyph: "spark", color: HEALTH_COLOR.ok, label: "Run started (launch)" },
  { glyph: "ring", color: HEALTH_COLOR.degraded, label: "Cluster degraded" },
  { glyph: "ring", color: HEALTH_COLOR.failing, label: "Cluster offline" },
];

/** Viewport width each planet column wants before another column fits. */
const MIN_COL_PX = 250;

function mix(color: string, pct: number, other = "transparent") {
  return `color-mix(in oklab, ${color} ${pct}%, ${other})`;
}

function truncate(text: string, max: number) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function position(s: OrbitSatellite, angle: number) {
  return `translate(${(s.cx + s.r * Math.cos(angle)).toFixed(2)},${(s.cy + s.r * Math.sin(angle)).toFixed(2)})`;
}

/** Pick columns from the container's width; SSR and jsdom get the default. */
function useColumns(ref: React.RefObject<HTMLDivElement | null>, count: number) {
  const [cols, setCols] = React.useState(Math.min(3, Math.max(1, count)));
  React.useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width;
      if (w > 0) setCols(Math.max(1, Math.min(count, Math.floor(w / MIN_COL_PX))));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, count]);
  return Math.max(1, Math.min(cols, count));
}

function Defs({ uid }: { uid: string }) {
  const healths: Health[] = ["ok", "degraded", "failing"];
  return (
    <defs>
      {/* The lit planet: a highlight toward the upper left, brand green body, limb falling to the card. */}
      <radialGradient id={`${uid}-planet`} cx="36%" cy="30%" r="75%">
        <stop
          offset="0%"
          style={{ stopColor: mix("var(--success)", 70, "var(--brand-primary)") }}
        />
        <stop offset="45%" style={{ stopColor: "var(--brand-primary)" }} />
        <stop offset="100%" style={{ stopColor: mix("var(--brand-primary)", 25, "var(--card)") }} />
      </radialGradient>
      <radialGradient id={`${uid}-planet-off`} cx="36%" cy="30%" r="75%">
        <stop
          offset="0%"
          style={{ stopColor: mix("var(--muted-foreground)", 55, "var(--card)") }}
        />
        <stop
          offset="100%"
          style={{ stopColor: mix("var(--muted-foreground)", 12, "var(--card)") }}
        />
      </radialGradient>
      {/* Atmosphere: brightness is the cluster's mean load, set by opacity. */}
      <radialGradient id={`${uid}-atmo`}>
        <stop offset="62%" style={{ stopColor: mix("var(--success)", 0) }} />
        <stop offset="78%" style={{ stopColor: mix("var(--success)", 38) }} />
        <stop offset="100%" style={{ stopColor: mix("var(--success)", 0) }} />
      </radialGradient>
      {healths.map((h) => (
        <radialGradient key={h} id={`${uid}-halo-${h}`}>
          <stop offset="0%" style={{ stopColor: mix(HEALTH_COLOR[h], 55) }} />
          <stop offset="100%" style={{ stopColor: mix(HEALTH_COLOR[h], 0) }} />
        </radialGradient>
      ))}
      <linearGradient id={`${uid}-trail`} x1="0" y1="1" x2="0" y2="0">
        <stop offset="0%" style={{ stopColor: mix(HEALTH_COLOR.ok, 0) }} />
        <stop offset="100%" style={{ stopColor: HEALTH_COLOR.ok }} />
      </linearGradient>
    </defs>
  );
}

function Planet({ planet, uid }: { planet: OrbitPlanet; uid: string }) {
  const { cluster, cx, cy } = planet;
  const offline = cluster.health === "offline";
  const lit = !offline && planet.meanLoad > 0.02;
  const nameY = cy + RING_R[2] + 26;
  return (
    <g>
      {Array.from({ length: planet.rings }, (_, i) => (
        <circle
          key={i}
          cx={cx}
          cy={cy}
          r={RING_R[i]}
          fill="none"
          stroke="var(--border)"
          strokeWidth={1}
          strokeDasharray={offline ? "2 4" : undefined}
        />
      ))}
      {planet.hasDecay && (
        <circle
          cx={cx}
          cy={cy}
          r={DECAY_R}
          fill="none"
          stroke={mix(HEALTH_COLOR.failing, 45)}
          strokeWidth={1}
          strokeDasharray="1 3"
        />
      )}
      {lit && (
        <circle
          cx={cx}
          cy={cy}
          r={PLANET_R * 1.6}
          fill={`url(#${uid}-atmo)`}
          opacity={0.25 + planet.meanLoad * 0.75}
        />
      )}
      <circle cx={cx} cy={cy} r={PLANET_R} fill={`url(#${uid}-planet${offline ? "-off" : ""})`} />
      {cluster.health !== "ok" && (
        <circle
          cx={cx}
          cy={cy}
          r={PLANET_R + 4}
          fill="none"
          stroke={offline ? HEALTH_COLOR.failing : HEALTH_COLOR.degraded}
          strokeWidth={1.5}
          strokeDasharray={offline ? "3 3" : undefined}
        />
      )}
      <text
        x={cx}
        y={nameY}
        textAnchor="middle"
        className="fill-foreground font-mono"
        fontSize={12}
        fontWeight={600}
      >
        <title>
          {cluster.name}
          {cluster.region ? ` (${cluster.region})` : ""}
        </title>
        {truncate(cluster.name, 26)}
      </text>
      <text
        x={cx}
        y={nameY + 15}
        textAnchor="middle"
        className="fill-muted-foreground font-mono"
        fontSize={10}
      >
        {planet.agents} agents · {planet.busy} busy
        {planet.failing > 0 && (
          <tspan style={{ fill: HEALTH_COLOR.failing }}> · {planet.failing} failing</tspan>
        )}
        {planet.queued > 0 && ` · ${planet.queued} queued`}
      </text>
    </g>
  );
}

interface SatelliteProps {
  sat: OrbitSatellite;
  uid: string;
  motion: FleetViewProps["motion"];
  selected: boolean;
  onSelect?: (id: string) => void;
  register: (id: string, el: SVGGElement | null) => void;
}

function Satellite({ sat, uid, motion, selected, onSelect, register }: SatelliteProps) {
  const { id } = sat;
  const nodeRef = React.useCallback((el: SVGGElement | null) => register(id, el), [register, id]);
  const color = HEALTH_COLOR[sat.health];
  const failing = sat.health === "failing";
  const core = 2.6 + sat.load * 1.6;
  const label = `${sat.name}: ${HEALTH_LABEL[sat.health]}, ${Math.round(sat.load * 100)}% load, ${sat.activeRuns} running, ${sat.queued} queued`;
  return (
    <g
      ref={nodeRef}
      transform={position(sat, sat.phase)}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={label}
      aria-pressed={onSelect ? selected : undefined}
      className={cn("group outline-none", onSelect && "cursor-pointer")}
      onClick={() => onSelect?.(sat.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(sat.id);
        }
      }}
    >
      <title>{label}</title>
      <circle r={9} fill="transparent" />
      <g className={failing && motion === "full" ? MOTION_CLASS.flicker : undefined}>
        {HEALTH_GLOWS[sat.health] && (
          <circle r={core * 3} fill={`url(#${uid}-halo-${sat.health})`} />
        )}
        <circle
          r={core}
          fill={sat.health === "idle" ? mix(color, 55, "var(--card)") : color}
          stroke={sat.health === "idle" ? mix(color, 70) : undefined}
          strokeWidth={sat.health === "idle" ? 0.8 : undefined}
        />
      </g>
      {failing && motion === "reduced" && (
        <circle r={core + 3} fill="none" stroke={HEALTH_COLOR.failing} strokeWidth={1.4} />
      )}
      {selected && (
        <circle r={core + 5.5} fill="none" stroke="var(--foreground)" strokeWidth={1.2} />
      )}
      <circle
        r={core + 7.5}
        fill="none"
        stroke="var(--ring)"
        strokeWidth={1.8}
        className="opacity-0 group-focus-visible:opacity-100"
      />
    </g>
  );
}

export function FleetOrbit({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const rootRef = React.useRef<HTMLDivElement>(null);
  const uid = `orbit${React.useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const cols = useColumns(rootRef, snapshot.clusters.length);
  const layout = React.useMemo(() => layoutOrbit(snapshot, cols), [snapshot, cols]);
  const launches = React.useMemo(
    () =>
      motion === "full"
        ? recentLaunches(snapshot, layout, LAUNCH_MS)
        : recentLaunches(snapshot, layout, EVENT_WINDOW_MS, true),
    [snapshot, layout, motion]
  );

  // The animation reads these; React never re-renders per frame.
  const nodes = React.useRef(new Map<string, SVGGElement>());
  const angles = React.useRef(new Map<string, number>());
  const sats = React.useRef(layout.satellites);
  const register = React.useCallback((id: string, el: SVGGElement | null) => {
    if (el) nodes.current.set(id, el);
    else nodes.current.delete(id);
  }, []);

  // After each render, put satellites back where the loop had them (React may
  // have written the still phase), or on their still phase when reduced.
  React.useLayoutEffect(() => {
    sats.current = layout.satellites;
    const live = new Set<string>();
    for (const s of layout.satellites) {
      live.add(s.id);
      if (motion === "reduced") angles.current.delete(s.id);
      else if (!angles.current.has(s.id)) angles.current.set(s.id, s.phase);
      nodes.current
        .get(s.id)
        ?.setAttribute("transform", position(s, angles.current.get(s.id) ?? s.phase));
    }
    for (const id of angles.current.keys()) if (!live.has(id)) angles.current.delete(id);
  });

  React.useEffect(() => {
    if (motion !== "full" || typeof window.requestAnimationFrame !== "function") return;
    let frame = 0;
    let last: number | null = null;
    const tick = (t: number) => {
      // Clamp the step so a backgrounded tab does not jump on return.
      const dt = last === null ? 0 : Math.min(0.1, (t - last) / 1000);
      last = t;
      for (const s of sats.current) {
        if (s.omega === 0) continue;
        const a = (angles.current.get(s.id) ?? s.phase) + s.omega * dt;
        angles.current.set(s.id, a % (Math.PI * 2));
        nodes.current.get(s.id)?.setAttribute("transform", position(s, a));
      }
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [motion]);

  const summary = summarizeFleet(snapshot);

  return (
    <div
      ref={rootRef}
      data-motion={motion}
      role="group"
      aria-label={summary}
      className={cn("w-full", className)}
    >
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        preserveAspectRatio="xMidYMin meet"
        className="mx-auto block h-auto w-full"
        style={{ maxWidth: layout.cols * CELL_W * 1.6 }}
      >
        <Defs uid={uid} />
        {layout.planets.map((p) => (
          <Planet key={p.cluster.id} planet={p} uid={uid} />
        ))}
        <g aria-hidden>
          {launches.map((l) =>
            motion === "full" ? (
              // Keyed by event id: the rise plays once, when the event arrives.
              <g key={l.id} transform={`translate(${l.x.toFixed(1)},${l.y.toFixed(1)})`}>
                <g className={MOTION_CLASS.rise}>
                  <rect x={-0.9} y={-14} width={1.8} height={14} fill={`url(#${uid}-trail)`} />
                  <circle cy={-15} r={2} fill={HEALTH_COLOR.ok} />
                </g>
              </g>
            ) : (
              <path
                key={l.id}
                transform={`translate(${l.x.toFixed(1)},${l.y.toFixed(1)})`}
                d="M0 -2 V-12 M-3.5 -8 L0 -13 L3.5 -8"
                fill="none"
                stroke={HEALTH_COLOR.ok}
                strokeWidth={1.6}
                strokeLinecap="round"
              />
            )
          )}
        </g>
        {layout.satellites.map((s) => (
          <Satellite
            key={s.id}
            sat={s}
            uid={uid}
            motion={motion}
            selected={s.id === selectedAgentId}
            onSelect={onSelectAgent}
            register={register}
          />
        ))}
      </svg>
    </div>
  );
}
