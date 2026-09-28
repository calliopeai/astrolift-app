"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { EVENT_WINDOW_MS, type FleetCluster, type FleetViewProps } from "../core/fleet-model";
import { HEALTH_COLOR, HEALTH_LABEL, MOTION_CLASS, type Health } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

import { hexPath, layoutHive, loadMix, truncate, type HiveCell } from "./hive-layout";

/**
 * Hive: one hexagon per agent, packed by cluster, for fleets of hundreds.
 * Fill brightness is load, the border is health, failing tiles flicker. A
 * dispatch rings out from the cluster's hub cell and a spark travels to the
 * agent that got the work. Reduced motion: no ripples or sparks; a small
 * still dot marks every agent that received work in the event window.
 */

export const FLEET_HIVE_LEGEND: LegendItem[] = [
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Brightness: load" },
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Border: healthy" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, label: "Degraded" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failing" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Dark: idle" },
  { glyph: "ring", label: "Ring: dispatch from the cluster hub" },
  { glyph: "dot", label: "Still dot: received work in the last 12s" },
];

/** How long after a dispatch its ripple is still drawn. */
const PULSE_MS = 1500;
/** Spark travel time from hub to tile. */
const TRAVEL_MS = 600;
/** Cap on drawn pulses per frame, so a 500-agent burst stays cheap. */
const MAX_PULSES = 48;

const CLUSTER_HEALTH: Record<FleetCluster["health"], Health> = {
  ok: "ok",
  degraded: "degraded",
  offline: "failing",
};

interface Pulse {
  id: string;
  from: HiveCell;
  to: HiveCell;
}

interface Spark {
  dot: SVGCircleElement | null;
  line: SVGLineElement | null;
  pulse: Pulse;
}

function useWidth(ref: React.RefObject<HTMLDivElement | null>, fallback: number) {
  const [width, setWidth] = React.useState(fallback);
  React.useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

export function FleetHive({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const rootRef = React.useRef<HTMLDivElement>(null);
  const width = useWidth(rootRef, 960);
  const { now, clusters, agents, events } = snapshot;
  const layout = React.useMemo(
    () => layoutHive(clusters, agents, width),
    [clusters, agents, width]
  );
  const { r } = layout;
  const tile = React.useMemo(() => hexPath(r * 0.9), [r]);
  const ring = React.useMemo(() => hexPath(r * 1.05), [r]);
  const reduced = motion === "reduced";

  const agentById = React.useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);
  const clusterName = React.useMemo(() => new Map(clusters.map((c) => [c.id, c.name])), [clusters]);

  // Agents that received work in the window (the reduced picture's dot).
  const received = React.useMemo(() => {
    const set = new Set<string>();
    for (const e of events) {
      if (e.kind === "dispatched" && now - e.at < EVENT_WINDOW_MS) set.add(e.agentId);
    }
    return set;
  }, [events, now]);

  // Fresh dispatches, newest first, capped.
  const pulses = React.useMemo<Pulse[]>(() => {
    if (reduced) return [];
    const out: Pulse[] = [];
    for (let i = events.length - 1; i >= 0 && out.length < MAX_PULSES; i--) {
      const e = events[i];
      if (e.kind !== "dispatched") continue;
      if (now - e.at >= PULSE_MS) break;
      const to = layout.byAgent.get(e.agentId);
      const group = to && layout.groups.find((g) => g.clusterId === to.clusterId);
      if (to && group) out.push({ id: e.id, from: group.hub, to });
    }
    return out;
  }, [events, now, layout, reduced]);

  // One hub ring per cluster per burst, keyed so it replays on a new burst.
  const hubRings = React.useMemo(() => {
    const latest = new Map<string, Pulse>();
    for (const p of pulses) if (!latest.has(p.from.clusterId)) latest.set(p.from.clusterId, p);
    return [...latest.values()];
  }, [pulses]);

  // Sparks move hub to tile on requestAnimationFrame, written straight to
  // the DOM through refs so React does not re-render every frame.
  // The trace line fades after arrival, so a stale snapshot leaves no lines.
  const sparks = React.useRef(new Map<string, Spark>());
  const started = React.useRef(new Map<string, number>());
  const sparkRef = React.useCallback(
    (pulse: Pulse, part: "dot" | "line") => (el: SVGCircleElement | SVGLineElement | null) => {
      const s = sparks.current.get(pulse.id) ?? { dot: null, line: null, pulse };
      if (part === "dot") s.dot = el as SVGCircleElement | null;
      else s.line = el as SVGLineElement | null;
      if (s.dot || s.line) sparks.current.set(pulse.id, s);
      else sparks.current.delete(pulse.id);
    },
    []
  );
  React.useEffect(() => {
    if (reduced || pulses.length === 0) return;
    const t = performance.now();
    const ids = new Set(pulses.map((p) => p.id));
    for (const id of started.current.keys()) if (!ids.has(id)) started.current.delete(id);
    for (const id of ids) if (!started.current.has(id)) started.current.set(id, t);
    let raf = 0;
    const tick = (now: number) => {
      let live = false;
      for (const [id, s] of sparks.current) {
        const t0 = started.current.get(id) ?? now;
        const p = Math.min(1, (now - t0) / TRAVEL_MS);
        const fade = Math.min(1, (now - t0) / PULSE_MS);
        const { from, to } = s.pulse;
        if (s.dot) {
          s.dot.setAttribute("cx", (from.x + (to.x - from.x) * p).toFixed(1));
          s.dot.setAttribute("cy", (from.y + (to.y - from.y) * p).toFixed(1));
          s.dot.setAttribute("opacity", p < 1 ? "1" : "0");
        }
        s.line?.setAttribute("stroke-opacity", (0.2 * (1 - fade)).toFixed(3));
        if (fade < 1) live = true;
      }
      if (live) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [pulses, reduced]);

  const counts = React.useMemo(() => {
    let failing = 0;
    let degraded = 0;
    let busy = 0;
    for (const a of agents) {
      if (a.health === "failing") failing++;
      else if (a.health === "degraded") degraded++;
      if (a.activeRuns > 0) busy++;
    }
    return { failing, degraded, busy };
  }, [agents]);

  const label =
    `${agents.length} agents in ${clusters.length} clusters, ` +
    `${counts.failing} failing, ${counts.degraded} degraded, ${counts.busy} busy`;

  const charsPerPx = 1 / 6.2;

  return (
    <div
      ref={rootRef}
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("w-full", className)}
    >
      <svg
        width="100%"
        viewBox={`-2 -2 ${layout.width + 4} ${layout.height + 4}`}
        className="block h-auto"
      >
        {layout.groups.map((g) => {
          const cluster = clusters.find((c) => c.id === g.clusterId);
          const health = CLUSTER_HEALTH[cluster?.health ?? "ok"];
          const count = g.cells.length;
          const suffix = ` · ${count}`;
          const name = truncate(
            g.name,
            Math.max(3, Math.floor(g.width * charsPerPx) - suffix.length)
          );
          return (
            <g key={g.clusterId}>
              <text
                x={g.x}
                y={g.y + 11}
                className="font-mono"
                fontSize={10}
                fill="var(--muted-foreground)"
              >
                <title>{`${g.name}${cluster?.region ? ` (${cluster.region})` : ""}, ${count} agents`}</title>
                {name}
                {suffix}
              </text>
              <g transform={`translate(${g.hub.x} ${g.hub.y})`}>
                <title>{`${g.name} dispatcher, cluster ${HEALTH_LABEL[health].toLowerCase()}`}</title>
                <path d={tile} fill="var(--card)" stroke={HEALTH_COLOR[health]} strokeWidth={1.5} />
                <path
                  d={hexPath(r * 0.4)}
                  fill={`color-mix(in oklab, ${HEALTH_COLOR[health]} 45%, var(--card))`}
                />
              </g>
            </g>
          );
        })}

        {layout.groups.flatMap((g) =>
          g.cells.map((cell) => {
            const a = agentById.get(cell.agentId as string);
            if (!a) return null;
            const color = HEALTH_COLOR[a.health];
            const selected = a.id === selectedAgentId;
            const select = () => onSelectAgent?.(a.id);
            const desc = `${a.name}, ${clusterName.get(a.clusterId) ?? a.clusterId}, ${HEALTH_LABEL[a.health].toLowerCase()}, load ${Math.round(a.load * 100)}%, ${a.activeRuns} running, ${a.queued} queued`;
            return (
              <g
                key={a.id}
                transform={`translate(${cell.x.toFixed(1)} ${cell.y.toFixed(1)})`}
                tabIndex={0}
                role="button"
                aria-label={desc}
                aria-pressed={selected}
                className="group cursor-pointer outline-none"
                onClick={select}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    select();
                  }
                }}
              >
                <title>{desc}</title>
                <path
                  d={tile}
                  fill={`color-mix(in oklab, ${color} ${loadMix(a.load)}%, var(--card))`}
                  stroke={color}
                  strokeWidth={a.health === "failing" ? 1.6 : 1}
                  className={a.health === "failing" ? MOTION_CLASS.flicker : undefined}
                />
                {selected && (
                  <path d={ring} fill="none" stroke="var(--brand-primary)" strokeWidth={2} />
                )}
                <path
                  d={ring}
                  fill="none"
                  stroke="var(--foreground)"
                  strokeWidth={1.5}
                  className="opacity-0 group-focus-visible:opacity-100"
                />
                {reduced && received.has(a.id) && (
                  <circle r={Math.max(1.5, r * 0.22)} fill="var(--foreground)" />
                )}
              </g>
            );
          })
        )}

        {!reduced && (
          <g aria-hidden pointerEvents="none">
            {hubRings.map((p) => (
              <circle
                key={`hub-${p.id}`}
                cx={p.from.x}
                cy={p.from.y}
                r={r * 0.8}
                fill="none"
                stroke="var(--foreground)"
                strokeWidth={1}
                opacity={0}
                className={MOTION_CLASS.ripple}
              />
            ))}
            {pulses.map((p) => (
              <React.Fragment key={p.id}>
                <line
                  ref={sparkRef(p, "line")}
                  x1={p.from.x}
                  y1={p.from.y}
                  x2={p.to.x}
                  y2={p.to.y}
                  stroke="var(--foreground)"
                  strokeOpacity={0.2}
                  strokeWidth={0.75}
                />
                <circle
                  ref={sparkRef(p, "dot")}
                  cx={p.from.x}
                  cy={p.from.y}
                  r={Math.max(1.5, r * 0.2)}
                  fill="var(--foreground)"
                />
                <circle
                  cx={p.to.x}
                  cy={p.to.y}
                  r={r * 0.6}
                  fill="none"
                  stroke="var(--foreground)"
                  strokeWidth={1}
                  opacity={0}
                  className={MOTION_CLASS.ripple}
                  style={{ animationDelay: `${TRAVEL_MS}ms`, animationFillMode: "both" }}
                />
              </React.Fragment>
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}
