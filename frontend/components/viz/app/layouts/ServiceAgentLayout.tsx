"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { AppNode, AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, MOTION_CLASS, flowDuration } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import { agentFeed, edgeFailing, lastCallByTarget, summarize, truncate } from "./layouts-b";
import { FOCUS_RING, StatusDot, tint } from "./layout-parts";
import { Flash, LoadBar, PhosphorDot, fmtRps, healthLabel, selectProps } from "./layouts-b-parts";

/**
 * App and agent, side by side (spec 44 viz addendum, auto layout for the
 * service-agent topology). The app's workloads and data sit left, the agent
 * and its outgoing calls right, and between them a live feed of what the
 * agent is doing, one row per agent_action event.
 *
 * Motion, and what it means:
 * - A feed row's dot fades like phosphor over the 12s event window.
 * - A tile the agent just called ripples once (app side or call list).
 * - A call line flows with its traffic; faster is busier; red is failing.
 * - A failing workload flickers.
 *
 * Reduced motion: no ripple, fade or flow. Feed dots and call rings are drawn
 * with a still opacity from their age, and call lines are solid bars whose
 * thickness carries the rate.
 */

export const SERVICE_AGENT_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Agent action, fades over 12s" },
  { glyph: "dot", color: HEALTH_COLOR.failing, label: "Agent action on a failing call" },
  { glyph: "ring", color: "var(--brand-primary)", label: "Just called by the agent" },
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Agent call traffic: faster is busier" },
  { glyph: "flow", color: HEALTH_COLOR.failing, label: "Call failing (over 5% errors)" },
  { glyph: "bar", color: HEALTH_COLOR.ok, label: "Load" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing workload" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle: stays dark" },
];

const FEED_ROWS = 10;

export type ServiceAgentLayoutProps = AppViewProps & { diagram: React.ReactNode };

export function ServiceAgentLayout({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
  diagram,
}: ServiceAgentLayoutProps) {
  const agents = snapshot.nodes.filter((n) => n.role === "agent");
  const agentIds = new Set(agents.map((a) => a.id));
  const callEdges = snapshot.edges.filter((e) => agentIds.has(e.from));
  const calledIds = new Set(callEdges.map((e) => e.to));
  const byId = new Map(snapshot.nodes.map((n) => [n.id, n]));
  const appNodes = snapshot.nodes.filter((n) => n.role !== "agent" && n.role !== "external");
  const feed = agentFeed(snapshot);
  const lastCall = lastCallByTarget(feed);
  const maxRps = Math.max(1, ...callEdges.map((e) => e.rps));

  const label = `${snapshot.app.name}: ${summarize(appNodes, "app workloads")}; ${summarize(
    agents,
    agents.length === 1 ? "agent" : "agents"
  )}; ${feed.length} agent actions in the last 12s`;

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("flex min-w-0 flex-col gap-3 p-3", className)}
    >
      <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1fr)]">
        <section className="min-w-0 space-y-2" aria-label="App">
          <h3 className="text-muted-foreground text-2xs font-mono tracking-wide uppercase">App</h3>
          {appNodes.map((n) => {
            const call = lastCall.get(n.id);
            return (
              <NodeTile
                key={n.id}
                node={n}
                called={calledIds.has(n.id)}
                flash={call && { id: call.id, age: call.age }}
                motion={motion}
                onSelect={onSelectNode}
              />
            );
          })}
        </section>

        <section className="min-w-0" aria-label="What the agent is doing">
          <h3 className="text-muted-foreground text-2xs mb-2 font-mono tracking-wide uppercase">
            What the agent is doing
          </h3>
          <ol className="bg-background/40 min-h-32 space-y-1 rounded-sm border p-2">
            {feed.length === 0 && (
              <li className="text-muted-foreground px-1 py-4 text-center text-xs">
                Quiet: no agent actions in the last 12s
              </li>
            )}
            {feed.slice(0, FEED_ROWS).map((f) => {
              const agent = byId.get(f.agentId);
              const text = `${agent?.name ?? f.agentId} ${f.verb} ${f.target?.name ?? "work"}`;
              return (
                <li
                  key={f.id}
                  className="flex min-w-0 items-center gap-2 font-mono text-xs"
                  title={text}
                >
                  <PhosphorDot
                    age={f.age}
                    color={f.failing ? HEALTH_COLOR.failing : HEALTH_COLOR.ok}
                  />
                  <span className="text-muted-foreground w-10 shrink-0 tabular-nums">
                    {Math.round(f.age / 1000)}s
                  </span>
                  <span className="min-w-0 truncate">
                    <span className="text-muted-foreground">{truncate(agent?.name ?? "", 24)}</span>{" "}
                    {f.verb}{" "}
                    <span style={{ color: f.failing ? HEALTH_COLOR.failing : undefined }}>
                      {f.target?.name ?? "work"}
                    </span>
                  </span>
                </li>
              );
            })}
          </ol>
        </section>

        <section className="min-w-0 space-y-2" aria-label="Agent">
          <h3 className="text-muted-foreground text-2xs font-mono tracking-wide uppercase">
            Agent
          </h3>
          {agents.map((a) => (
            <NodeTile key={a.id} node={a} called={false} motion={motion} onSelect={onSelectNode} />
          ))}
          <ul className="space-y-1.5" aria-label="Agent calls">
            {callEdges.map((e) => {
              const target = byId.get(e.to);
              if (!target) return null;
              const call = lastCall.get(target.id);
              const failing = edgeFailing(e);
              const color = failing ? HEALTH_COLOR.failing : HEALTH_COLOR[target.health];
              const rate = e.rps / maxRps;
              const moving = motion === "full" && flowParticles && e.rps > 0;
              return (
                <li key={`${e.from}-${e.to}`}>
                  <div
                    {...selectProps(target.id, onSelectNode)}
                    aria-label={`Agent calls ${target.name}: ${e.rps}/s, ${(e.errorRate * 100).toFixed(1)}% errors`}
                    className={cn(
                      "relative flex items-center gap-2 rounded-sm border px-2 py-1.5",
                      FOCUS_RING
                    )}
                    style={{ borderColor: failing ? tint(HEALTH_COLOR.failing, 60) : undefined }}
                  >
                    <Flash
                      eventId={call?.id}
                      age={call?.age ?? Infinity}
                      motion={motion}
                      color="var(--brand-primary)"
                    />
                    <svg viewBox="0 0 40 8" className="h-2 w-10 shrink-0" aria-hidden>
                      <line
                        x1="0"
                        y1="4"
                        x2="40"
                        y2="4"
                        stroke={e.rps > 0 ? color : HEALTH_COLOR.idle}
                        strokeWidth={1 + rate * 3}
                        strokeLinecap="round"
                        className={moving ? MOTION_CLASS.flow : undefined}
                        style={
                          moving
                            ? ({ "--viz-flow-duration": flowDuration(rate) } as React.CSSProperties)
                            : undefined
                        }
                      />
                    </svg>
                    <span className="min-w-0 flex-1 truncate text-xs" title={target.name}>
                      {target.name}
                    </span>
                    <span className="text-muted-foreground shrink-0 font-mono text-xs tabular-nums">
                      {fmtRps(e.rps)}
                    </span>
                    {failing && (
                      <span
                        className="shrink-0 font-mono text-xs"
                        style={{ color: HEALTH_COLOR.failing }}
                      >
                        {(e.errorRate * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      </div>

      {diagram != null && (
        <div className="h-72 min-w-0 overflow-hidden rounded-sm border">{diagram}</div>
      )}
    </div>
  );
}

function NodeTile({
  node,
  called,
  flash,
  motion,
  onSelect,
}: {
  node: AppNode;
  called: boolean;
  flash?: { id: string; age: number };
  motion: "full" | "reduced";
  onSelect?: (id: string) => void;
}) {
  const failing = node.health === "failing";
  return (
    <div
      {...selectProps(node.id, onSelect)}
      aria-label={`${node.name}, ${node.kind}, ${healthLabel(node)}, load ${Math.round(node.load * 100)}%${called ? ", called by the agent" : ""}`}
      className={cn("bg-card relative space-y-1.5 rounded-sm border px-2.5 py-2", FOCUS_RING)}
      style={{
        borderColor: failing
          ? tint(HEALTH_COLOR.failing, 70)
          : called
            ? tint("var(--brand-primary)", 55)
            : undefined,
      }}
    >
      <Flash
        eventId={flash?.id}
        age={flash?.age ?? Infinity}
        motion={motion}
        color="var(--brand-primary)"
      />
      <div className="flex min-w-0 items-center gap-2">
        <StatusDot health={node.health} />
        <span className="min-w-0 flex-1 truncate text-sm" title={node.name}>
          {node.name}
        </span>
        <span className="text-muted-foreground shrink-0 font-mono text-xs">{node.kind}</span>
      </div>
      <LoadBar node={node} />
      <div className="text-muted-foreground flex gap-3 font-mono text-xs tabular-nums">
        {node.rps !== undefined && <span>{fmtRps(node.rps)}</span>}
        {node.p50 !== undefined && <span>p50 {node.p50}ms</span>}
        {node.replicas && (
          <span>
            {node.replicas.ready}/{node.replicas.desired} ready
          </span>
        )}
        {called && <span style={{ color: "var(--brand-primary)" }}>agent</span>}
      </div>
    </div>
  );
}
