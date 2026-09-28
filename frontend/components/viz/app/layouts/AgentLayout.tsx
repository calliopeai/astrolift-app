"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { AppNode, AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, MOTION_CLASS, flowDuration } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import {
  EVENT_WINDOW_MS,
  FLASH_MS,
  agentFeed,
  edgeFailing,
  formatCountdown,
  lastCallByTarget,
  nextRun,
  parseSchedule,
  recency,
  summarize,
  truncate,
} from "./layouts-b";
import { fmtRps, healthLabel, selectProps, tint } from "./layouts-b-parts";

/**
 * An agent app (spec 44 viz addendum, auto layout for the agent topology).
 * Runs and tool calls come first: a strip per agent with a tick for every
 * action in the last 12s, then the agent's calls fanning out to its external
 * tools. The diagram is small, beside the fan-out.
 *
 * Motion, and what it means:
 * - Ticks slide left as they age and dim with recency; "now" is the right edge.
 * - A fan-out line flows with the call rate; red when over 5% errors.
 * - A tool the agent just called ripples once.
 * - A failing agent or tool flickers.
 *
 * Reduced motion: ticks sit still at their age with the same dimming, lines
 * are solid with thickness for rate, and a just-called tool keeps a still
 * ring dimmed by how long ago the call was.
 */

export const AGENT_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "bar", color: HEALTH_COLOR.ok, label: "Agent action (tick), dims over 12s" },
  { glyph: "bar", color: HEALTH_COLOR.failing, label: "Action on a failing call" },
  {
    glyph: "flow",
    color: HEALTH_COLOR.ok,
    label: "Tool call traffic: faster and thicker is busier",
  },
  { glyph: "flow", color: HEALTH_COLOR.failing, label: "Tool call failing (over 5% errors)" },
  { glyph: "ring", color: "var(--brand-primary)", label: "Tool just called" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing agent or tool" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle: stays dark" },
];

const W = 600;
const ROW = 44;
const AGENT_X = 12;
const AGENT_W = 170;
const TOOL_X = 410;
const TOOL_W = 178;
const TILE_H = 32;

export type AgentLayoutProps = AppViewProps & { diagram: React.ReactNode };

export function AgentLayout({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
  diagram,
}: AgentLayoutProps) {
  const agents = snapshot.nodes.filter((n) => n.role === "agent");
  const agentIds = new Set(agents.map((a) => a.id));
  const byId = new Map(snapshot.nodes.map((n) => [n.id, n]));
  const callEdges = snapshot.edges.filter((e) => agentIds.has(e.from) && byId.has(e.to));
  const tools = [...new Set(callEdges.map((e) => e.to))]
    .map((id) => byId.get(id))
    .filter((n): n is AppNode => !!n && !agentIds.has(n.id));
  const schedules = snapshot.nodes.filter((n) => n.role === "schedule" || n.role === "trigger");
  const feed = agentFeed(snapshot);
  const lastCall = lastCallByTarget(feed);
  const maxRps = Math.max(1, ...callEdges.map((e) => e.rps));

  const rows = Math.max(agents.length, tools.length, 1);
  const H = rows * ROW + 12;
  const yOf = (i: number, n: number) => 6 + ((rows - n) * ROW) / 2 + i * ROW + ROW / 2;
  const agentY = new Map(agents.map((a, i) => [a.id, yOf(i, agents.length)]));
  const toolY = new Map(tools.map((t, i) => [t.id, yOf(i, tools.length)]));

  const label = `${snapshot.app.name}: ${summarize(agents, agents.length === 1 ? "agent" : "agents")}; ${feed.length} actions in the last 12s; ${summarize(tools, "tools")}`;

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("flex min-w-0 flex-col gap-3 p-3", className)}
    >
      <section aria-label="Runs" className="min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-muted-foreground text-2xs font-mono tracking-wide uppercase">
            Actions, last 12s
          </h3>
          <div className="text-muted-foreground flex flex-wrap gap-3 font-mono text-xs">
            {schedules.map((s) => {
              const sched = parseSchedule(s.name);
              const next = sched && nextRun(sched, snapshot.now);
              return (
                <span key={s.id} className="truncate" title={s.name}>
                  {truncate(s.name, 28)}
                  {next ? ` · next run in ${formatCountdown(next - snapshot.now)}` : ""}
                </span>
              );
            })}
          </div>
        </div>
        {agents.map((a) => (
          <RunStrip
            key={a.id}
            agent={a}
            ticks={feed.filter((f) => f.agentId === a.id)}
            motion={motion}
            onSelect={onSelectNode}
          />
        ))}
      </section>

      <div
        className={cn(
          "grid min-w-0 gap-3",
          diagram != null && "sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"
        )}
      >
        <section aria-label="Tool calls" className="min-w-0">
          <h3 className="text-muted-foreground text-2xs mb-1.5 font-mono tracking-wide uppercase">
            Tool calls
          </h3>
          <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full">
            {callEdges.map((e) => {
              const ya = agentY.get(e.from);
              const yt = toolY.get(e.to);
              if (ya === undefined || yt === undefined) return null;
              const failing = edgeFailing(e);
              const rate = e.rps / maxRps;
              const idle = e.rps === 0 || byId.get(e.from)?.health === "idle";
              const color = failing
                ? HEALTH_COLOR.failing
                : idle
                  ? HEALTH_COLOR.idle
                  : HEALTH_COLOR.ok;
              const x1 = AGENT_X + AGENT_W;
              const mid = (x1 + TOOL_X) / 2;
              const d = `M${x1},${ya} C${mid},${ya} ${mid},${yt} ${TOOL_X},${yt}`;
              const moving = motion === "full" && flowParticles && !idle;
              return (
                <g key={`${e.from}-${e.to}`}>
                  <path d={d} fill="none" stroke={tint(color, 30)} strokeWidth={1 + rate * 4} />
                  {moving && (
                    <path
                      d={d}
                      fill="none"
                      stroke={color}
                      strokeWidth={2}
                      strokeLinecap="round"
                      className={MOTION_CLASS.flow}
                      style={{ "--viz-flow-duration": flowDuration(rate) } as React.CSSProperties}
                    />
                  )}
                  <text
                    x={mid}
                    y={(ya + yt) / 2 - 4}
                    textAnchor="middle"
                    className="font-mono"
                    fontSize="10"
                    fill={failing ? HEALTH_COLOR.failing : "var(--muted-foreground)"}
                  >
                    {fmtRps(e.rps)}
                    {failing ? ` · ${(e.errorRate * 100).toFixed(0)}% err` : ""}
                  </text>
                </g>
              );
            })}
            {agents.map((a) => (
              <Tile
                key={a.id}
                node={a}
                x={AGENT_X}
                y={agentY.get(a.id) ?? 0}
                w={AGENT_W}
                sub={`load ${Math.round(a.load * 100)}%`}
                onSelect={onSelectNode}
              />
            ))}
            {tools.map((t) => {
              const call = lastCall.get(t.id);
              const y = toolY.get(t.id) ?? 0;
              return (
                <g key={t.id}>
                  <Tile
                    node={t}
                    x={TOOL_X}
                    y={y}
                    w={TOOL_W}
                    sub={t.rps !== undefined ? fmtRps(t.rps) : t.kind}
                    onSelect={onSelectNode}
                  />
                  {call && motion === "full" && call.age <= FLASH_MS && (
                    <circle
                      key={call.id}
                      cx={TOOL_X}
                      cy={y}
                      r={6}
                      fill="none"
                      stroke="var(--brand-primary)"
                      strokeWidth={2}
                      className={MOTION_CLASS.ripple}
                    />
                  )}
                  {call && motion === "reduced" && call.age <= EVENT_WINDOW_MS && (
                    <circle
                      cx={TOOL_X}
                      cy={y}
                      r={8}
                      fill="none"
                      stroke="var(--brand-primary)"
                      strokeWidth={2}
                      opacity={recency(call.age)}
                    />
                  )}
                </g>
              );
            })}
          </svg>
        </section>
        {diagram != null && (
          <section aria-label="Diagram" className="min-w-0">
            <h3 className="text-muted-foreground text-2xs mb-1.5 font-mono tracking-wide uppercase">
              Diagram
            </h3>
            <div className="max-h-64 overflow-hidden rounded-sm border">{diagram}</div>
          </section>
        )}
      </div>
    </div>
  );
}

function RunStrip({
  agent,
  ticks,
  motion,
  onSelect,
}: {
  agent: AppNode;
  ticks: ReturnType<typeof agentFeed>;
  motion: "full" | "reduced";
  onSelect?: (id: string) => void;
}) {
  const failing = agent.health === "failing";
  return (
    <div
      {...selectProps(agent.id, onSelect)}
      aria-label={`${agent.name}: ${ticks.length} actions in the last 12s, ${healthLabel(agent)}`}
      className="focus-visible:ring-ring grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-2 rounded-sm outline-none focus-visible:ring-2"
    >
      <span className="flex min-w-0 items-center gap-1.5 text-xs" title={agent.name}>
        <span
          aria-hidden
          className={cn("size-2 shrink-0 rounded-full", failing && MOTION_CLASS.flicker)}
          style={{ background: HEALTH_COLOR[agent.health] }}
        />
        <span className="truncate font-mono">{agent.name}</span>
      </span>
      <span
        className="bg-background/40 relative block h-7 overflow-hidden rounded-sm border"
        style={{ borderColor: failing ? tint(HEALTH_COLOR.failing, 60) : undefined }}
      >
        {ticks.map((t) => (
          <span
            key={t.id}
            aria-hidden
            title={`${t.verb} ${t.target?.name ?? "work"}, ${Math.round(t.age / 1000)}s ago`}
            className="absolute inset-y-1 w-0.5 -translate-x-1/2 rounded-sm"
            style={{
              left: `${Math.max(0, 100 - (t.age / EVENT_WINDOW_MS) * 100)}%`,
              background: t.failing ? HEALTH_COLOR.failing : HEALTH_COLOR.ok,
              opacity: recency(t.age),
              transition: motion === "full" ? "left 1.2s linear, opacity 1.2s linear" : undefined,
            }}
          />
        ))}
        {ticks.length === 0 && (
          <span className="text-muted-foreground absolute inset-0 flex items-center justify-center text-xs">
            no actions
          </span>
        )}
      </span>
    </div>
  );
}

function Tile({
  node,
  x,
  y,
  w,
  sub,
  onSelect,
}: {
  node: AppNode;
  x: number;
  y: number;
  w: number;
  sub: string;
  onSelect?: (id: string) => void;
}) {
  const color = HEALTH_COLOR[node.health];
  const failing = node.health === "failing";
  return (
    <g
      {...selectProps(node.id, onSelect)}
      aria-label={`${node.name}, ${healthLabel(node)}`}
      className="group cursor-pointer outline-none"
    >
      <title>{node.name}</title>
      <rect
        x={x - 3}
        y={y - TILE_H / 2 - 3}
        width={w + 6}
        height={TILE_H + 6}
        rx={3}
        fill="none"
        stroke="var(--ring)"
        strokeWidth={2}
        className="opacity-0 group-focus-visible:opacity-100"
      />
      <rect
        x={x}
        y={y - TILE_H / 2}
        width={w}
        height={TILE_H}
        rx={2}
        fill="var(--card)"
        stroke={failing ? color : "var(--border)"}
      />
      <rect
        x={x}
        y={y - TILE_H / 2}
        width={3}
        height={TILE_H}
        fill={color}
        className={failing ? MOTION_CLASS.flicker : undefined}
      />
      <text x={x + 10} y={y - 2} fontSize="12" fill="var(--foreground)">
        {truncate(node.name, Math.floor(w / 8))}
      </text>
      <text
        x={x + 10}
        y={y + 11}
        fontSize="10"
        className="font-mono"
        fill="var(--muted-foreground)"
      >
        {sub}
      </text>
    </g>
  );
}
