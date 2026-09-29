"use client";

import * as React from "react";

import { TOPOLOGY_META } from "@/lib/topology";
import { cn } from "@/lib/utils";

import type { AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, HEALTH_LABEL } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import { DiagramSlot, FlowLine, FOCUS_RING, StatusDot, tint } from "./layout-parts";
import {
  appTotals,
  edgeRows,
  fmtMs,
  fmtPct,
  fmtRps,
  hotSpots,
  nodesWithRole,
} from "./layout-metrics";

/**
 * Auto layout for microservices: the service map is the page, large, with a
 * one-line summary above it. Beside it, hot spots rank nodes by inbound
 * error rate then p50, and a table lists every call path by requests/s.
 * Reduced motion: the call-path dashes stand still (weight is the share of
 * the busiest path) and failing dots stop flickering; rank, colour and the
 * numbers carry the same state.
 */

export const MICROSERVICES_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Healthy" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, label: "Degraded: over 1% errors" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failing, over 5% errors" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Dark: no traffic" },
  { glyph: "bar", color: HEALTH_COLOR.failing, label: "Hot spot bar: share of calls failing" },
  {
    glyph: "flow",
    color: HEALTH_COLOR.ok,
    label: "Moving dash: calls on a path, faster when busier",
  },
];

/** Call paths shown before the table folds the rest into a count. */
const EDGE_ROWS = 12;

export type MicroservicesLayoutProps = AppViewProps & { diagram: React.ReactNode };

export function MicroservicesLayout({
  snapshot,
  motion,
  flowParticles = true,
  onSelectNode,
  className,
  diagram,
}: MicroservicesLayoutProps) {
  const totals = appTotals(snapshot);
  const services = nodesWithRole(snapshot, "service");
  const hot = hotSpots(snapshot, 5);
  const rows = edgeRows(snapshot);
  const shown = rows.slice(0, EDGE_ROWS);
  const failingPaths = rows.filter((r) => r.health === "failing").length;
  const failingNodes = snapshot.nodes.filter((n) => n.health === "failing").length;
  const worst = hot[0];

  const label =
    `${snapshot.app.name}, ${TOPOLOGY_META[snapshot.topology].label.toLowerCase()}: ` +
    `${services.length} services, ${failingNodes} failing, ${failingPaths} failing call paths, ` +
    `${fmtRps(totals.rps)} requests per second` +
    (worst && worst.health !== "ok" && worst.health !== "idle"
      ? `, hottest ${worst.node.name} at ${fmtPct(worst.errorRate)} errors`
      : "");

  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn("@container w-full space-y-3", className)}
    >
      <dl className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs">
        {[
          ["services", `${services.length}`],
          ["req/s", fmtRps(totals.rps)],
          ["p50", fmtMs(totals.p50)],
          ["errors", fmtPct(totals.errorRate)],
          ["replicas", `${totals.replicas.ready}/${totals.replicas.desired}`],
          ["failing paths", `${failingPaths}`],
        ].map(([k, v]) => (
          <div key={k} className="flex gap-1.5">
            <dt>{k}</dt>
            <dd className="text-foreground tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="grid gap-3 @4xl:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
        <DiagramSlot className="h-80 @2xl:h-96">{diagram}</DiagramSlot>
        <section className="min-w-0" aria-label="Hot spots">
          <h3 className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            Hot spots
          </h3>
          <ol className="grid gap-1.5 @2xl:@max-4xl:grid-cols-2">
            {hot.map((h, i) => {
              const color = HEALTH_COLOR[h.health];
              return (
                <li key={h.node.id} className="min-w-0">
                  <button
                    type="button"
                    tabIndex={0}
                    title={`${h.node.name} (${h.node.kind}): ${HEALTH_LABEL[h.health]}`}
                    onClick={() => onSelectNode?.(h.node.id)}
                    className={cn(
                      "bg-card flex w-full min-w-0 flex-col gap-1.5 rounded-sm border px-2.5 py-2 text-left",
                      FOCUS_RING
                    )}
                    style={{
                      borderColor:
                        h.health === "failing" || h.health === "degraded"
                          ? tint(color, 60)
                          : "var(--border)",
                    }}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="text-muted-foreground w-4 shrink-0 font-mono text-xs">
                        {i + 1}
                      </span>
                      <StatusDot health={h.health} />
                      <span className="truncate font-mono text-sm">{h.node.name}</span>
                      <span className="text-muted-foreground ml-auto shrink-0 font-mono text-xs tabular-nums">
                        {fmtPct(h.errorRate)} · {fmtMs(h.node.p50)}
                      </span>
                    </span>
                    <span aria-hidden className="bg-muted block h-1 overflow-hidden rounded-sm">
                      <span
                        className="block h-full"
                        style={{
                          width: `${Math.min(100, Math.max(h.errorRate > 0 ? 2 : 0, h.errorRate * 400))}%`,
                          background: HEALTH_COLOR[h.errorRate > 0.01 ? h.health : "idle"],
                        }}
                      />
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      </div>
      <section className="min-w-0 overflow-x-auto" aria-label="Call paths">
        <h3 className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
          Call paths by requests/s
        </h3>
        <table className="w-full table-fixed border-collapse text-sm">
          <thead>
            <tr className="text-muted-foreground border-b text-left text-xs">
              <th className="w-1/3 py-1.5 pr-2 font-medium">From → to</th>
              <th className="py-1.5 pr-2 font-medium">Traffic</th>
              <th className="w-20 py-1.5 pr-2 text-right font-medium">req/s</th>
              <th className="w-20 py-1.5 text-right font-medium">errors</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={`${r.edge.from}-${r.edge.to}`} className="border-b border-dashed">
                <td className="py-1.5 pr-2">
                  <span
                    className="flex min-w-0 items-center gap-1.5 font-mono text-xs"
                    title={`${r.fromName} → ${r.toName}`}
                  >
                    <StatusDot health={r.health} />
                    <span className="truncate">
                      {r.fromName} → {r.toName}
                    </span>
                  </span>
                </td>
                <td className="py-1.5 pr-2">
                  <FlowLine
                    rps={r.edge.rps}
                    share={r.share}
                    health={r.health}
                    flowParticles={flowParticles}
                  />
                </td>
                <td className="py-1.5 pr-2 text-right font-mono text-xs tabular-nums">
                  {fmtRps(r.edge.rps)}
                </td>
                <td
                  className="py-1.5 text-right font-mono text-xs tabular-nums"
                  style={{
                    color:
                      r.health === "failing" || r.health === "degraded"
                        ? HEALTH_COLOR[r.health]
                        : undefined,
                  }}
                >
                  {fmtPct(r.edge.errorRate)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > shown.length && (
          <p className="text-muted-foreground mt-1.5 text-xs">
            and {rows.length - shown.length} quieter paths
          </p>
        )}
      </section>
    </div>
  );
}
