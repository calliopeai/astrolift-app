"use client";

import * as React from "react";

import { RadialGauge } from "@/components/viz/radial-gauge";
import { TOPOLOGY_META } from "@/lib/topology";
import { cn } from "@/lib/utils";

import type { AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, HEALTH_LABEL, type Health } from "../../core/semantics";
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
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Healthy" },
  {
    glyph: "dot",
    color: HEALTH_COLOR.degraded,
    label: "Degraded: over 1% errors or replicas missing",
  },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failing, over 5% errors" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Dark: no traffic" },
  {
    glyph: "bar",
    color: HEALTH_COLOR.ok,
    label: "Squares: replicas ready; a breathing outline is one not ready yet",
  },
];

export type ServiceLayoutProps = AppViewProps & { diagram: React.ReactNode };

export function ServiceLayout({
  snapshot,
  motion,
  onSelectNode,
  className,
  diagram,
}: ServiceLayoutProps) {
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

  const label =
    `${snapshot.app.name}, ${TOPOLOGY_META[snapshot.topology].label.toLowerCase()}: ` +
    `${HEALTH_LABEL[totals.health].toLowerCase()}, ${fmtRps(totals.rps)} requests per second, ` +
    `p50 ${fmtMs(totals.p50)}, ${fmtPct(totals.errorRate)} errors, ` +
    `${totals.replicas.ready} of ${totals.replicas.desired} replicas ready`;

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
            label="Requests/s"
            value={fmtRps(totals.rps)}
            health={trafficHealth}
            series={series.rps}
          />
          <Kpi
            label="p50 latency"
            value={fmtMs(totals.p50)}
            health={trafficHealth}
            series={series.p50}
          />
          <Kpi
            label="Error rate"
            value={fmtPct(totals.errorRate)}
            health={errHealth}
            series={series.err}
          />
          <Kpi
            label="Replicas ready"
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
                    ariaLabel={`${node.name} CPU`}
                  />
                </span>
                <span className="grid min-w-0 flex-1 grid-cols-3 gap-2">
                  <Figure label="req/s" value={fmtRps(node.rps ?? s.inRps)} />
                  <Figure label="p50" value={fmtMs(node.p50)} />
                  <Figure label="errors" value={fmtPct(s.errorRate)} />
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
