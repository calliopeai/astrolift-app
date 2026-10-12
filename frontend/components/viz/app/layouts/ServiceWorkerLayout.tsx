"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { RadialGauge } from "@/components/viz/radial-gauge";
import { Sparkline } from "@/components/viz/sparkline";
import { cn } from "@/lib/utils";

import type { AppNode, AppSnapshot, AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, MOTION_CLASS, type Health } from "../../core/semantics";
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
  { glyph: "dot", color: HEALTH_COLOR.ok, labelKey: "healthy" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, labelKey: "swDegraded" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, labelKey: "flickerFailing" },
  { glyph: "dot", color: HEALTH_COLOR.idle, labelKey: "darkNoTraffic" },
  { glyph: "bar", color: HEALTH_COLOR.ok, labelKey: "swTank" },
  { glyph: "flow", color: HEALTH_COLOR.ok, labelKey: "swDash" },
  { glyph: "ring", color: HEALTH_COLOR.ok, labelKey: "swGauge" },
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
  const t = useTranslations("vizRender");
  const s = nodeStats(snapshot, node);
  const r = node.replicas ?? { ready: 0, desired: 0 };
  return (
    <NodeButton node={node} health={s.health} onSelect={onSelect}>
      <span className="flex items-center gap-3">
        <span style={{ color: HEALTH_COLOR[s.health] }}>
          <RadialGauge
            value={node.load}
            size={44}
            thickness={5}
            ariaLabel={t("cpuOf", { name: node.name })}
          />
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
  const t = useTranslations("vizRender");
  const color = HEALTH_COLOR[q.health];
  const lag =
    q.net > 0
      ? t("serviceWorker.behind", { rps: fmtRps(q.net) })
      : q.inRps > 0
        ? t("serviceWorker.keepingUp")
        : t("serviceWorker.empty");
  return (
    <div className="flex min-w-0 items-stretch gap-2">
      <div className="flex w-10 shrink-0 flex-col justify-center gap-0.5 @2xl:w-16">
        <FlowLine
          rps={q.inRps}
          share={max > 0 ? q.inRps / max : 0}
          health={q.health === "failing" ? "failing" : q.inRps > 0 ? "ok" : "idle"}
          flowParticles={flowParticles}
        />
        <span className="text-muted-foreground text-center font-mono text-xs">
          {t("serviceWorker.in")}
        </span>
      </div>
      <button
        type="button"
        tabIndex={0}
        title={t("nodeTitle", {
          name: q.node.name,
          kind: q.node.kind,
          health: t(`health.${q.health}`),
        })}
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
            <Figure label={t("serviceWorker.depth")} value={fmtPct(q.fill)} emphasis />
            <Figure label={t("serviceWorker.lag")} value={lag} />
            <span style={{ color }}>
              <Sparkline
                data={depthSeries}
                width={96}
                height={20}
                variant="area"
                ariaLabel={t("serviceWorker.depthTrend")}
              />
            </span>
          </span>
        </span>
        <span className="grid grid-cols-2 gap-2">
          <Figure label={t("serviceWorker.enqueued")} value={fmtRps(q.inRps)} />
          <Figure label={t("serviceWorker.consumed")} value={fmtRps(q.outRps)} />
        </span>
      </button>
      <div className="flex w-10 shrink-0 flex-col justify-center gap-0.5 @2xl:w-16">
        <FlowLine
          rps={q.outRps}
          share={max > 0 ? q.outRps / max : 0}
          health={q.outRps > 0 ? "ok" : "idle"}
          flowParticles={flowParticles}
        />
        <span className="text-muted-foreground text-center font-mono text-xs">
          {t("serviceWorker.out")}
        </span>
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
  const t = useTranslations("vizRender");
  const totals = appTotals(snapshot);
  const web = nodesWithRole(snapshot, "service");
  const workers = nodesWithRole(snapshot, "worker");
  const q = queueStats(snapshot);
  const max = maxEdgeRps(snapshot);
  const series = useSeries(snapshot.now, { depth: q?.fill ?? 0 });
  const throughput = q?.outRps ?? 0;

  const queueHealth: Health = q?.health ?? "idle";
  const head = t("trafficSummary", {
    app: snapshot.app.name,
    topology: t(`topology.${snapshot.topology}`),
    health: t(`healthInline.${totals.health}`),
    rps: fmtRps(totals.rps),
    errors: fmtPct(totals.errorRate),
  });
  const label = q
    ? t("serviceWorker.summary", {
        head,
        queue: q.node.name,
        fill: fmtPct(q.fill),
        health: t(`healthInline.${queueHealth}`),
        inRps: fmtRps(q.inRps),
        outRps: fmtRps(q.outRps),
      }) + (q.net > 0 ? t("serviceWorker.fallingBehind", { rps: fmtRps(q.net) }) : "")
    : t("serviceWorker.summaryNoQueue", { head });

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("@container w-full space-y-3", className)}
    >
      <div className="grid gap-3 @2xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,2fr)] @2xl:items-center">
        <section className="grid min-w-0 gap-2" aria-label={t("serviceWorker.web")}>
          <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            {t("serviceWorker.webHeading", {
              rps: fmtRps(totals.rps),
              errors: fmtPct(totals.errorRate),
            })}
          </h3>
          {web.map((node) => (
            <Workload
              key={node.id}
              snapshot={snapshot}
              node={node}
              rateLabel={t("reqPerSec")}
              rate={node.rps ?? 0}
              onSelect={onSelectNode}
            />
          ))}
        </section>
        <section className="grid min-w-0 gap-2" aria-label={t("serviceWorker.queue")}>
          <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            {t("serviceWorker.queue")}
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
            <p className="text-muted-foreground text-sm">{t("serviceWorker.noQueue")}</p>
          )}
        </section>
        <section className="grid min-w-0 gap-2" aria-label={t("serviceWorker.workers")}>
          <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            {t("serviceWorker.workersHeading", { rps: fmtRps(throughput) })}
          </h3>
          {workers.map((node) => (
            <Workload
              key={node.id}
              snapshot={snapshot}
              node={node}
              rateLabel={t("jobsPerSec")}
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
