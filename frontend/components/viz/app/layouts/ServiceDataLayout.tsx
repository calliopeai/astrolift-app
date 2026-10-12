"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { RadialGauge } from "@/components/viz/radial-gauge";
import { cn } from "@/lib/utils";

import type { AppNode, AppSnapshot, AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, type Health } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import {
  DiagramSlot,
  Figure,
  FlowLine,
  Kpi,
  NodeButton,
  ReplicaPips,
  useSeries,
} from "./layout-parts";
import {
  appTotals,
  fmtMs,
  fmtPct,
  fmtRps,
  healthForErrorRate,
  maxEdgeRps,
  nodesWithRole,
  nodeStats,
} from "./layout-metrics";

/**
 * Auto layout for a service with its data: the service KPIs on top, then the
 * service and each datastore side by side, joined by the live calls between
 * them, with the diagram below at medium size. A datastore's gauge is its
 * connection load. Reduced motion: the traffic dashes stand still, their
 * weight and the printed rate still say how busy each link is; failing and
 * missing replicas keep their colour and outline without moving.
 */

export const SERVICE_DATA_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, labelKey: "healthy" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, labelKey: "degradedErrorsReplicas" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, labelKey: "flickerFailingErrors" },
  { glyph: "dot", color: HEALTH_COLOR.idle, labelKey: "darkNoTraffic" },
  { glyph: "flow", color: HEALTH_COLOR.ok, labelKey: "sdDash" },
  { glyph: "ring", color: HEALTH_COLOR.ok, labelKey: "sdGauge" },
  { glyph: "bar", color: HEALTH_COLOR.ok, labelKey: "squaresReplicas" },
];

export type ServiceDataLayoutProps = AppViewProps & { diagram: React.ReactNode };

function Store({
  snapshot,
  node,
  fromId,
  max,
  flowParticles,
  onSelect,
}: {
  snapshot: AppSnapshot;
  node: AppNode;
  fromId: string | undefined;
  max: number;
  flowParticles: boolean;
  onSelect?: (id: string) => void;
}) {
  const t = useTranslations("vizRender");
  const s = nodeStats(snapshot, node);
  const edge = snapshot.edges.find((e) => e.to === node.id && e.from === fromId);
  const rps = edge?.rps ?? s.inRps;
  const linkHealth = edge ? healthForErrorRate(edge.errorRate, edge.rps) : s.health;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="flex w-16 shrink-0 flex-col items-center gap-0.5 @2xl:w-24">
        <FlowLine
          rps={rps}
          share={max > 0 ? rps / max : 0}
          health={linkHealth}
          flowParticles={flowParticles}
        />
        <span className="text-muted-foreground font-mono text-xs tabular-nums">
          {fmtRps(rps)}/s
        </span>
      </div>
      <NodeButton node={node} health={s.health} onSelect={onSelect} className="flex-1">
        <span className="flex items-center gap-3">
          <span style={{ color: HEALTH_COLOR[s.health] }}>
            <RadialGauge
              value={node.load}
              size={44}
              thickness={5}
              ariaLabel={t(node.kind === "redis" ? "loadOf" : "connectionsOf", { name: node.name })}
            />
          </span>
          <span className="grid min-w-0 flex-1 grid-cols-2 gap-2">
            <Figure
              label={node.kind === "redis" ? t("load") : t("serviceData.connections")}
              value={fmtPct(node.load)}
            />
            <Figure label={t("serviceData.callErrors")} value={fmtPct(s.errorRate)} />
          </span>
        </span>
      </NodeButton>
    </div>
  );
}

export function ServiceDataLayout({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
  diagram,
}: ServiceDataLayoutProps) {
  const t = useTranslations("vizRender");
  const totals = appTotals(snapshot);
  const services = nodesWithRole(snapshot, "service");
  const stores = nodesWithRole(snapshot, "data");
  const primary = services[0];
  const max = maxEdgeRps(snapshot);
  const series = useSeries(snapshot.now, {
    rps: totals.rps,
    p50: totals.p50 ?? 0,
    err: totals.errorRate,
  });
  const errHealth = healthForErrorRate(totals.errorRate, totals.rps);
  const trafficHealth: Health = totals.rps > 0 ? "ok" : "idle";
  const replicaHealth: Health =
    totals.replicas.ready < totals.replicas.desired ? "degraded" : trafficHealth;
  const worstStore = stores
    .map((n) => nodeStats(snapshot, n))
    .find((s) => s.health === "failing" || s.health === "degraded");

  const label =
    t("serviceData.summary", {
      head: t("trafficSummary", {
        app: snapshot.app.name,
        topology: t(`topology.${snapshot.topology}`),
        health: t(`healthInline.${totals.health}`),
        rps: fmtRps(totals.rps),
        errors: fmtPct(totals.errorRate),
      }),
      count: stores.length,
    }) +
    (worstStore
      ? t("serviceData.worstStore", {
          name: worstStore.node.name,
          health: t(`healthInline.${worstStore.health}`),
        })
      : "") +
    stores.map((n) => t("serviceData.storeAt", { name: n.name, load: fmtPct(n.load) })).join("");

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("@container w-full space-y-3", className)}
    >
      <div className="grid grid-cols-2 gap-3 @2xl:grid-cols-4">
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
        />
      </div>
      <div className="grid items-center gap-3 @2xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="grid gap-3">
          {services.map((node) => {
            const s = nodeStats(snapshot, node);
            const r = node.replicas ?? { ready: 0, desired: 0 };
            return (
              <NodeButton key={node.id} node={node} health={s.health} onSelect={onSelectNode}>
                <span className="flex items-center gap-3">
                  <span style={{ color: HEALTH_COLOR[s.health] }}>
                    <RadialGauge
                      value={node.load}
                      size={44}
                      thickness={5}
                      ariaLabel={t("cpuOf", { name: node.name })}
                    />
                  </span>
                  <span className="grid min-w-0 flex-1 grid-cols-2 gap-2">
                    <Figure label={t("reqPerSec")} value={fmtRps(node.rps ?? s.inRps)} />
                    <Figure label="p50" value={fmtMs(node.p50)} />
                  </span>
                </span>
                <ReplicaPips ready={r.ready} desired={r.desired} health={s.health} />
              </NodeButton>
            );
          })}
        </div>
        <div className="grid gap-3">
          {stores.map((node) => (
            <Store
              key={node.id}
              snapshot={snapshot}
              node={node}
              fromId={primary?.id}
              max={max}
              flowParticles={flowParticles}
              onSelect={onSelectNode}
            />
          ))}
        </div>
      </div>
      <DiagramSlot className="h-56">{diagram}</DiagramSlot>
    </div>
  );
}
