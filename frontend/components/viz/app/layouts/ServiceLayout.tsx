"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { RadialGauge } from "@/components/viz/radial-gauge";
import { cn } from "@/lib/utils";

import type { AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, type Health } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import { DiagramSlot, Figure, Kpi, NodeButton, ReplicaPips, useSeries } from "./layout-parts";
import {
  appTotals,
  fmtMs,
  fmtPct,
  fmtRps,
  healthForErrorRate,
  nodesWithRole,
  nodeStats,
} from "./layout-metrics";

/**
 * Auto layout for a single service: the four numbers that matter (requests/s,
 * p50, error rate, replicas ready) lead, the diagram is small beside them,
 * and each service gets a card with its replicas and CPU. Reduced motion:
 * the flicker and breathing stop; the dot colour, the outlined (missing)
 * replica squares and the numbers carry the same state.
 */

export const SERVICE_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, labelKey: "healthy" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, labelKey: "degradedErrorsReplicas" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, labelKey: "flickerFailingErrors" },
  { glyph: "dot", color: HEALTH_COLOR.idle, labelKey: "darkNoTraffic" },
  { glyph: "bar", color: HEALTH_COLOR.ok, labelKey: "squaresReplicas" },
];

export type ServiceLayoutProps = AppViewProps & { diagram: React.ReactNode };

export function ServiceLayout({
  snapshot,
  motion,
  onSelectNode,
  className,
  diagram,
}: ServiceLayoutProps) {
  const t = useTranslations("vizRender");
  const totals = appTotals(snapshot);
  const services = nodesWithRole(snapshot, "service");
  const series = useSeries(snapshot.now, {
    rps: totals.rps,
    p50: totals.p50 ?? 0,
    err: totals.errorRate,
  });
  const errHealth = healthForErrorRate(totals.errorRate, totals.rps);
  const replicaHealth: Health =
    totals.replicas.desired === 0
      ? "idle"
      : totals.replicas.ready === 0
        ? "failing"
        : totals.replicas.ready < totals.replicas.desired
          ? "degraded"
          : "ok";
  const trafficHealth: Health = totals.rps > 0 ? "ok" : "idle";

  const label = t("service.summary", {
    app: snapshot.app.name,
    topology: t(`topology.${snapshot.topology}`),
    health: t(`healthInline.${totals.health}`),
    rps: fmtRps(totals.rps),
    p50: fmtMs(totals.p50),
    errors: fmtPct(totals.errorRate),
    ready: totals.replicas.ready,
    desired: totals.replicas.desired,
  });

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("@container w-full space-y-3", className)}
    >
      <div className="grid gap-3 @2xl:grid-cols-3">
        <div
          className={cn(
            "grid grid-cols-2 gap-3",
            diagram == null ? "@2xl:col-span-3" : "@2xl:col-span-2"
          )}
        >
          <Kpi
            label={t("kpi.requests")}
            value={fmtRps(totals.rps)}
            health={trafficHealth}
            series={series.rps}
          />
          <Kpi
            label={t("kpi.p50")}
            value={fmtMs(totals.p50)}
            health={trafficHealth}
            series={series.p50}
          />
          <Kpi
            label={t("kpi.errorRate")}
            value={fmtPct(totals.errorRate)}
            health={errHealth}
            series={series.err}
          />
          <Kpi
            label={t("kpi.replicas")}
            value={`${totals.replicas.ready}/${totals.replicas.desired}`}
            health={replicaHealth}
            caption={
              <ReplicaPips
                ready={totals.replicas.ready}
                desired={totals.replicas.desired}
                health={replicaHealth}
              />
            }
          />
        </div>
        <DiagramSlot className="h-44 @2xl:h-auto @2xl:min-h-44">{diagram}</DiagramSlot>
      </div>
      <div className="grid gap-3 @2xl:grid-cols-2">
        {services.map((node) => {
          const s = nodeStats(snapshot, node);
          const r = node.replicas ?? { ready: 0, desired: 0 };
          return (
            <NodeButton key={node.id} node={node} health={s.health} onSelect={onSelectNode}>
              <span className="flex items-center gap-4">
                <span style={{ color: HEALTH_COLOR[s.health] }}>
                  <RadialGauge
                    value={node.load}
                    size={48}
                    thickness={5}
                    ariaLabel={t("cpuOf", { name: node.name })}
                  />
                </span>
                <span className="grid min-w-0 flex-1 grid-cols-3 gap-2">
                  <Figure label={t("reqPerSec")} value={fmtRps(node.rps ?? s.inRps)} />
                  <Figure label="p50" value={fmtMs(node.p50)} />
                  <Figure label={t("errors")} value={fmtPct(s.errorRate)} />
                </span>
              </span>
              <ReplicaPips ready={r.ready} desired={r.desired} health={s.health} />
            </NodeButton>
          );
        })}
      </div>
    </div>
  );
}
