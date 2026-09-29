"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { EVENT_WINDOW_MS, type FleetAgent, type FleetViewProps } from "../core/fleet-model";
import { HEALTH_COLOR, HEALTH_GLOWS, HEALTH_LABEL, MOTION_CLASS } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import {
  HUB,
  PULSE_MS,
  VIEW_H,
  VIEW_W,
  placeHives,
  quadPoint,
  seedSwarmer,
  swarmSpeed,
  swarmTarget,
  tendrilWidth,
  type HivePlace,
  type SwarmerSeed,
} from "./swarm-layout";

/**
 * Swarm: each cluster is a hive and its agents swarm around it on trailing
 * tendrils. Every movement is state: an agent circles its hive as fast as it
 * is busy (idle agents settle close and hang still); a tendril is as thick as
 * the agent's active runs and trails behind it; a failing agent drifts to the
 * edge of the swarm and its tendril frays; a dispatch sends a pulse down the
 * tendrils, hub to hive to agent.
 *
 * One requestAnimationFrame loop writes positions and curves through refs;
 * React renders only when the snapshot changes. Reduced motion draws the same
 * swarm still, with recent dispatches as a dot on the agent's tendril.
 */

export const FLEET_SWARM_LEGEND: LegendItem[] = [
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Swarm speed: how busy the agent is" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Settled and still: idle" },
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Tendril thickness: active runs" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Drifting out, frayed tendril: failing" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, label: "Degraded: over 85% load" },
  { glyph: "spark", color: "var(--brand-primary)", label: "Pulse down the tendrils: a dispatch" },
];

const mix = (c: string, pct: number) => `color-mix(in oklab, ${c} ${pct}%, transparent)`;

interface Body {
  angle: number;
  x: number;
  y: number;
  /** The tendril's control point; lags the agent so the tendril trails. */
  cx: number;
  cy: number;
}

function curve(
  a: { x: number; y: number },
  c: { x: number; y: number },
  b: { x: number; y: number }
) {
  return `M${a.x.toFixed(1)},${a.y.toFixed(1)} Q${c.x.toFixed(1)},${c.y.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`;
}

/** A control point off the chord's midpoint, bent sideways by `bend` px. */
function bent(a: { x: number; y: number }, b: { x: number; y: number }, bend: number) {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return { x: mx - (dy / len) * bend, y: my + (dx / len) * bend };
}

function summarize(agents: FleetAgent[]): string {
  const failing = agents.filter((a) => a.health === "failing").length;
  const busy = agents.filter((a) => a.activeRuns > 0).length;
  const idle = agents.filter((a) => a.health === "idle").length;
  return `${agents.length} agents swarming: ${busy} busy, ${idle} idle, ${failing} failing`;
}

export function FleetSwarm({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const hives = React.useMemo(() => placeHives(snapshot), [snapshot]);
  const hiveById = React.useMemo(() => new Map(hives.map((h) => [h.id, h])), [hives]);
  const seeds = React.useMemo(() => {
    const byCluster = new Map<string, FleetAgent[]>();
    for (const a of snapshot.agents) {
      const list = byCluster.get(a.clusterId) ?? [];
      list.push(a);
      byCluster.set(a.clusterId, list);
    }
    const out = new Map<string, SwarmerSeed>();
    for (const list of byCluster.values()) {
      list.forEach((a, i) => out.set(a.id, seedSwarmer(a, i, list.length)));
    }
    return out;
  }, [snapshot.agents]);

  // Where everything rests, which is also the whole picture under reduced motion.
  const rest = React.useMemo(() => {
    const bodies = new Map<string, Body>();
    for (const a of snapshot.agents) {
      const hive = hiveById.get(a.clusterId);
      const seed = seeds.get(a.id);
      if (!hive || !seed) continue;
      const p = swarmTarget(a, hive, seed, seed.phase, 0);
      const c = bent(hive, p, 14 * (seed.spread - 0.9));
      bodies.set(a.id, { angle: seed.phase, x: p.x, y: p.y, cx: c.x, cy: c.y });
    }
    return bodies;
  }, [snapshot.agents, hiveById, seeds]);

  const bodiesRef = React.useRef(new Map<string, Body>());
  const agentEls = React.useRef(new Map<string, SVGGElement>());
  const tendrilEls = React.useRef(new Map<string, SVGPathElement>());
  const hubEls = React.useRef(new Map<string, SVGPathElement>());
  const pulseEls = React.useRef(new Map<string, SVGCircleElement>());
  const stateRef = React.useRef({ snapshot, hives, hiveById, seeds, at: 0 });

  React.useEffect(() => {
    stateRef.current = {
      snapshot,
      hives,
      hiveById,
      seeds,
      at: typeof performance !== "undefined" ? performance.now() : 0,
    };
    // New agents start at rest; existing ones keep swarming from where they are.
    const bodies = bodiesRef.current;
    for (const [id, b] of rest) if (!bodies.has(id)) bodies.set(id, { ...b });
    for (const id of [...bodies.keys()]) if (!rest.has(id)) bodies.delete(id);
  }, [snapshot, hives, hiveById, seeds, rest]);

  React.useEffect(() => {
    if (motion === "reduced" || typeof window.requestAnimationFrame !== "function") return;
    let raf = 0;
    let last = performance.now();
    const start = last;
    const frame = (ts: number) => {
      const dt = Math.min(0.05, (ts - last) / 1000);
      last = ts;
      const t = (ts - start) / 1000;
      const s = stateRef.current;
      const follow = 1 - Math.exp(-dt * 4);
      const trail = 1 - Math.exp(-dt * 1.6);
      const hubControls = new Map<string, { x: number; y: number }>();

      for (const hive of s.hives) {
        const agents = s.snapshot.agents.filter((a) => a.clusterId === hive.id);
        const load = agents.length ? agents.reduce((m, a) => m + a.load, 0) / agents.length : 0;
        const c = bent(HUB, hive, Math.sin(t * 0.7 + hive.x * 0.01) * 18 * load);
        hubControls.set(hive.id, c);
        hubEls.current.get(hive.id)?.setAttribute("d", curve(HUB, c, hive));
      }

      for (const a of s.snapshot.agents) {
        const hive = s.hiveById.get(a.clusterId);
        const seed = s.seeds.get(a.id);
        const b = bodiesRef.current.get(a.id);
        if (!hive || !seed || !b) continue;
        b.angle += swarmSpeed(a) * dt;
        const target = swarmTarget(a, hive, seed, b.angle, t);
        b.x += (target.x - b.x) * follow;
        b.y += (target.y - b.y) * follow;
        const sway =
          a.health === "idle"
            ? 0
            : Math.sin(t * seed.wobble * 1.3 + seed.phase) * (8 + a.load * 20);
        const want = bent(hive, b, sway);
        b.cx += (want.x - b.cx) * trail;
        b.cy += (want.y - b.cy) * trail;
        agentEls.current
          .get(a.id)
          ?.setAttribute("transform", `translate(${b.x.toFixed(1)},${b.y.toFixed(1)})`);
        tendrilEls.current.get(a.id)?.setAttribute("d", curve(hive, { x: b.cx, y: b.cy }, b));
      }

      // Pulses: hub → hive on the first half, hive → agent on the second.
      const simNow = s.snapshot.now + (ts - s.at);
      for (const e of s.snapshot.events) {
        if (e.kind !== "dispatched") continue;
        const el = pulseEls.current.get(e.id);
        if (!el) continue;
        const p = (simNow - e.at) / PULSE_MS;
        const agent = s.snapshot.agents.find((x) => x.id === e.agentId);
        const hive = agent && s.hiveById.get(agent.clusterId);
        const b = bodiesRef.current.get(e.agentId);
        if (!hive || !b || p < 0 || p > 1) {
          el.setAttribute("opacity", "0");
          continue;
        }
        const pt =
          p < 0.5
            ? quadPoint(HUB, hubControls.get(hive.id) ?? bent(HUB, hive, 0), hive, p * 2)
            : quadPoint(hive, { x: b.cx, y: b.cy }, b, (p - 0.5) * 2);
        el.setAttribute("cx", pt.x.toFixed(1));
        el.setAttribute("cy", pt.y.toFixed(1));
        el.setAttribute("opacity", String(p > 0.9 ? (1 - p) * 10 : 1));
      }
      raf = window.requestAnimationFrame(frame);
    };
    raf = window.requestAnimationFrame(frame);
    return () => window.cancelAnimationFrame(raf);
  }, [motion]);

  const reduced = motion === "reduced";
  const recentDispatch = (agentId: string) =>
    snapshot.events.some(
      (e) =>
        e.kind === "dispatched" && e.agentId === agentId && snapshot.now - e.at < EVENT_WINDOW_MS
    );
  const pulses = snapshot.events.filter(
    (e) => e.kind === "dispatched" && snapshot.now - e.at < PULSE_MS + 2000
  );

  return (
    <div
      role="group"
      aria-label={summarize(snapshot.agents)}
      data-motion={motion}
      className={cn("h-full w-full", className)}
    >
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid meet"
        className="block h-full max-h-[70vh] w-full"
      >
        <defs>
          <radialGradient id="swarm-hive">
            <stop offset="0%" stopColor="var(--brand-primary)" stopOpacity="0.28" />
            <stop offset="70%" stopColor="var(--brand-primary)" stopOpacity="0.06" />
            <stop offset="100%" stopColor="var(--brand-primary)" stopOpacity="0" />
          </radialGradient>
        </defs>

        {hives.map((hive) => (
          <path
            key={`hub-${hive.id}`}
            ref={(el) => {
              if (el) hubEls.current.set(hive.id, el);
              else hubEls.current.delete(hive.id);
            }}
            d={curve(HUB, bent(HUB, hive, 0), hive)}
            fill="none"
            stroke={mix("var(--brand-primary)", 45)}
            strokeWidth={1.5}
            strokeLinecap="round"
          />
        ))}

        {hives.map((hive) => (
          <HiveMark
            key={hive.id}
            hive={hive}
            health={snapshot.clusters.find((c) => c.id === hive.id)?.health ?? "ok"}
          />
        ))}

        {snapshot.agents.map((a) => {
          const hive = hiveById.get(a.clusterId);
          const b = rest.get(a.id);
          if (!hive || !b) return null;
          const color = HEALTH_COLOR[a.health];
          return (
            <path
              key={`t-${a.id}`}
              ref={(el) => {
                if (el) tendrilEls.current.set(a.id, el);
                else tendrilEls.current.delete(a.id);
              }}
              d={curve(hive, { x: b.cx, y: b.cy }, b)}
              fill="none"
              stroke={mix(color, a.health === "idle" ? 30 : 60)}
              strokeWidth={tendrilWidth(a.activeRuns)}
              strokeLinecap="round"
              strokeDasharray={a.health === "failing" ? "3 5" : undefined}
              className={a.health === "failing" ? MOTION_CLASS.flicker : undefined}
            />
          );
        })}

        {!reduced &&
          pulses.map((e) => (
            <circle
              key={e.id}
              ref={(el) => {
                if (el) pulseEls.current.set(e.id, el);
                else pulseEls.current.delete(e.id);
              }}
              r={4}
              opacity={0}
              fill="var(--brand-primary)"
              style={{ filter: `drop-shadow(0 0 6px ${mix("var(--brand-primary)", 80)})` }}
            />
          ))}

        {snapshot.agents.map((a) => {
          const b = rest.get(a.id);
          if (!b) return null;
          const color = HEALTH_COLOR[a.health];
          const selected = a.id === selectedAgentId;
          const r = 4 + Math.min(5, a.activeRuns * 1.5) + a.load * 2;
          const label = `${a.name}: ${HEALTH_LABEL[a.health]}, ${Math.round(a.load * 100)}% load, ${a.activeRuns} running, ${a.queued} queued`;
          return (
            <g
              key={a.id}
              ref={(el) => {
                if (el) agentEls.current.set(a.id, el);
                else agentEls.current.delete(a.id);
              }}
              transform={`translate(${b.x.toFixed(1)},${b.y.toFixed(1)})`}
              role="button"
              tabIndex={0}
              aria-label={label}
              aria-pressed={selected}
              className="cursor-pointer outline-none [&:focus-visible>circle.ring]:opacity-100"
              onClick={() => onSelectAgent?.(a.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectAgent?.(a.id);
                }
              }}
            >
              <title>{label}</title>
              <circle
                className="ring"
                r={r + 5}
                fill="none"
                stroke="var(--ring)"
                strokeWidth={2}
                opacity={selected ? 1 : 0}
              />
              <circle
                r={r}
                fill={color}
                className={a.health === "failing" ? MOTION_CLASS.flicker : undefined}
                style={
                  HEALTH_GLOWS[a.health]
                    ? { filter: `drop-shadow(0 0 ${3 + a.load * 5}px ${mix(color, 70)})` }
                    : undefined
                }
              />
              {reduced && recentDispatch(a.id) && (
                <circle r={2.5} cx={-(r + 6)} fill="var(--brand-primary)" />
              )}
            </g>
          );
        })}

        <g transform={`translate(${HUB.x},${HUB.y})`} aria-hidden>
          <circle r={16} fill={mix("var(--brand-primary)", 25)} />
          <circle r={7} fill="var(--brand-primary)" />
        </g>
      </svg>
    </div>
  );
}

function HiveMark({ hive, health }: { hive: HivePlace; health: "ok" | "degraded" | "offline" }) {
  const ring =
    health === "offline"
      ? HEALTH_COLOR.failing
      : health === "degraded"
        ? HEALTH_COLOR.degraded
        : "var(--border)";
  return (
    <g aria-hidden>
      <circle cx={hive.x} cy={hive.y} r={hive.r * 1.35} fill="url(#swarm-hive)" />
      <circle
        cx={hive.x}
        cy={hive.y}
        r={9}
        fill="var(--card)"
        stroke={ring}
        strokeWidth={2}
        strokeDasharray={health === "offline" ? "3 3" : undefined}
      />
      <circle
        cx={hive.x}
        cy={hive.y}
        r={3.5}
        fill={health === "offline" ? "var(--muted-foreground)" : "var(--brand-primary)"}
      />
      <text
        x={hive.x}
        y={hive.y + hive.r * 1.35 + 16}
        textAnchor="middle"
        className="fill-muted-foreground font-mono"
        fontSize={13}
        stroke="var(--card)"
        strokeWidth={5}
        strokeLinejoin="round"
        paintOrder="stroke"
      >
        {hive.name.length > 22 ? `${hive.name.slice(0, 21)}…` : hive.name}
      </text>
    </g>
  );
}
