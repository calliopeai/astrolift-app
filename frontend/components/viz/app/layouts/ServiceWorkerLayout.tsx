"use client";

import * as React from "react";

import { RadialGauge } from "@/components/viz/radial-gauge";
import { Sparkline } from "@/components/viz/sparkline";
import { TOPOLOGY_META } from "@/lib/topology";
import { cn } from "@/lib/utils";

import type { AppNode, AppSnapshot, AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, HEALTH_LABEL, MOTION_CLASS, type Health } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import {
  DiagramSlot,
  Figure,
  FlowLine,
  FOCUS_RING,
  NodeButton,
  ReplicaPips,
  StatusDot,
  tint,
  useSeries,
} from "./layout-parts";
import {
  appTotals,
  fmtMs,
  fmtPct,
  fmtRps,
  maxEdgeRps,
  nodesWithRole,
  nodeStats,
  queueStats,
  type QueueStats,
} from "./layout-metrics";

/**
 * Auto layout for web + worker: web on the left, the queue large in the
 * middle, workers on the right, with the diagram as a strip below. The queue
 * is the point: its tank fills with depth, enqueue and consume rates flow in
 * and out, and lag is enqueued minus consumed. A queue gaining messages past
 * half full is held work, so its fill breathes. Reduced motion: the fill,
 * the still dashes (weight is rate) and the numbers show the same backlog.
 */

export const SERVICE_WORKER_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Healthy" },
  {
    glyph: "dot",
    color: HEALTH_COLOR.degraded,
    label: "Degraded: backing up, over 1% errors or replicas missing",
  },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failing" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Dark: no traffic" },
  {
    glyph: "bar",
    color: HEALTH_COLOR.ok,
    label: "Tank: queue depth; it breathes while backing up",
  },
  {
    glyph: "flow",
    color: HEALTH_COLOR.ok,
    label: "Moving dash: messages in and out, faster when busier",
  },
  { glyph: "ring", color: HEALTH_COLOR.ok, label: "Gauge: CPU" },
];

export type ServiceWorkerLayoutProps = AppViewProps & { diagram: React.ReactNode };

function Workload({
  snapshot,
  node,
  rateLabel,
  rate,
  onSelect,
}: {
  snapshot: AppSnapshot;
  node: AppNode;
  rateLabel: string;
  rate: number;
  onSelect?: (id: string) => void;
}) {
  const s = nodeStats(snapshot, node);
  const r = node.replicas ?? { ready: 0, desired: 0 };
  return (
    <NodeButton node={node} health={s.health} onSelect={onSelect}>
      <span className="flex items-center gap-3">
        <span style={{ color: HEALTH_COLOR[s.health] }}>
          <RadialGauge value={node.load} size={44} thickness={5} ariaLabel={`${node.name} CPU`} />
        </span>
        <span className="grid min-w-0 flex-1 gap-1">
          <Figure label={rateLabel} value={fmtRps(rate)} />
          {node.p50 !== undefined && <Figure label="p50" value={fmtMs(node.p50)} />}
        </span>
      </span>
      <span className="flex items-center justify-between gap-2">
        <ReplicaPips ready={r.ready} desired={r.desired} health={s.health} />
        <span className="text-muted-foreground font-mono text-xs">
          {r.ready}/{r.desired}
        </span>
      </span>
    </NodeButton>
  );
}

function QueueTank({
  q,
  depthSeries,
  max,
  flowParticles,
  onSelect,
}: {
  q: QueueStats;
  depthSeries: number[];
  max: number;
  flowParticles: boolean;
  onSelect?: (id: string) => void;
}) {
  const color = HEALTH_COLOR[q.health];
  const lag = q.net > 0 ? `+${fmtRps(q.net)}/s behind` : q.inRps > 0 ? "keeping up" : "empty";
  return (
    <div className="flex min-w-0 items-stretch gap-2">
      <div className="flex w-10 shrink-0 flex-col justify-center gap-0.5 @2xl:w-16">
        <FlowLine
          rps={q.inRps}
          share={max > 0 ? q.inRps / max : 0}
          health={q.health === "failing" ? "failing" : q.inRps > 0 ? "ok" : "idle"}
          flowParticles={flowParticles}
        />
        <span className="text-muted-foreground text-center font-mono text-xs">in</span>
      </div>
      <button
        type="button"
        tabIndex={0}
        title={`${q.node.name} (${q.node.kind}): ${HEALTH_LABEL[q.health]}`}
        onClick={() => onSelect?.(q.node.id)}
        className={cn(
          "bg-card flex min-w-0 flex-1 flex-col gap-3 rounded-sm border-2 p-3 text-left",
          FOCUS_RING
        )}
        style={{ borderColor: q.health === "idle" ? "var(--border)" : tint(color, 70) }}
      >
        <span className="flex min-w-0 items-center gap-2">
          <StatusDot health={q.health} />
          <span className="truncate font-mono text-sm font-semibold">{q.node.name}</span>
          <span className="text-muted-foreground ml-auto shrink-0 font-mono text-xs">
            {q.node.kind}
          </span>
        </span>
        <span className="flex items-end gap-3">
          <span
            aria-hidden
            className="relative block h-32 w-14 shrink-0 overflow-hidden rounded-sm border"
            style={{ borderColor: tint(color, 50) }}
          >
            <span
              className={cn(
                "absolute inset-x-0 bottom-0 block",
                q.backingUp && MOTION_CLASS.breathe
              )}
              style={{
                height: `${Math.round(q.fill * 100)}%`,
                background: tint(color, q.health === "idle" ? 25 : 55),
              }}
            />
            <span
              className="absolute inset-x-0 block border-t border-dashed"
              style={{ bottom: "50%", borderColor: "var(--muted-foreground)" }}
            />
          </span>
          <span className="grid min-w-0 flex-1 gap-2">
            <Figure label="depth" value={fmtPct(q.fill)} emphasis />
            <Figure label="lag" value={lag} />
            <span style={{ color }}>
              <Sparkline
                data={depthSeries}
                width={96}
                height={20}
                variant="area"
                ariaLabel="Depth trend"
              />
            </span>
          </span>
        </span>
        <span className="grid grid-cols-2 gap-2">
          <Figure label="enqueued/s" value={fmtRps(q.inRps)} />
          <Figure label="consumed/s" value={fmtRps(q.outRps)} />
        </span>
      </button>
      <div className="flex w-10 shrink-0 flex-col justify-center gap-0.5 @2xl:w-16">
        <FlowLine
          rps={q.outRps}
          share={max > 0 ? q.outRps / max : 0}
          health={q.outRps > 0 ? "ok" : "idle"}
          flowParticles={flowParticles}
        />
        <span className="text-muted-foreground text-center font-mono text-xs">out</span>
      </div>
    </div>
  );
}

export function ServiceWorkerLayout({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
  diagram,
}: ServiceWorkerLayoutProps) {
  const totals = appTotals(snapshot);
  const web = nodesWithRole(snapshot, "service");
  const workers = nodesWithRole(snapshot, "worker");
  const q = queueStats(snapshot);
  const max = maxEdgeRps(snapshot);
  const series = useSeries(snapshot.now, { depth: q?.fill ?? 0 });
  const throughput = q?.outRps ?? 0;

  const queueHealth: Health = q?.health ?? "idle";
  const label =
    `${snapshot.app.name}, ${TOPOLOGY_META[snapshot.topology].label.toLowerCase()}: ` +
    `${HEALTH_LABEL[totals.health].toLowerCase()}, ${fmtRps(totals.rps)} requests per second, ` +
    `${fmtPct(totals.errorRate)} errors` +
    (q
      ? `, queue ${q.node.name} ${fmtPct(q.fill)} full and ${HEALTH_LABEL[queueHealth].toLowerCase()}, ` +
        `${fmtRps(q.inRps)} in and ${fmtRps(q.outRps)} out per second` +
        (q.net > 0 ? `, falling behind by ${fmtRps(q.net)} per second` : "")
      : ", no queue");

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("@container w-full space-y-3", className)}
    >
      <div className="grid gap-3 @2xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,2fr)] @2xl:items-center">
        <section className="grid min-w-0 gap-2" aria-label="Web">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Web · {fmtRps(totals.rps)} req/s · {fmtPct(totals.errorRate)} errors
          </h3>
          {web.map((node) => (
            <Workload
              key={node.id}
              snapshot={snapshot}
              node={node}
              rateLabel="req/s"
              rate={node.rps ?? 0}
              onSelect={onSelectNode}
            />
          ))}
        </section>
        <section className="grid min-w-0 gap-2" aria-label="Queue">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Queue
          </h3>
          {q ? (
            <QueueTank
              q={q}
              depthSeries={series.depth}
              max={max}
              flowParticles={flowParticles}
              onSelect={onSelectNode}
            />
          ) : (
            <p className="text-muted-foreground text-sm">No queue in this app.</p>
          )}
        </section>
        <section className="grid min-w-0 gap-2" aria-label="Workers">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Workers · {fmtRps(throughput)} jobs/s
          </h3>
          {workers.map((node) => (
            <Workload
              key={node.id}
              snapshot={snapshot}
              node={node}
              rateLabel="jobs/s"
              rate={nodeStats(snapshot, node).inRps}
              onSelect={onSelectNode}
            />
          ))}
        </section>
      </div>
      <DiagramSlot className="h-40">{diagram}</DiagramSlot>
    </div>
  );
}
