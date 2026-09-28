"use client";

import { BotIcon } from "lucide-react";
import * as React from "react";

import { DataTable, type Column } from "@/components/data-table";
import { cn } from "@/lib/utils";

import type { FleetAgent, FleetViewProps } from "../core/fleet-model";
import { HEALTH_COLOR, HEALTH_LABEL, MOTION_CLASS, type Health } from "../core/semantics";
import { staticController } from "../core/static-table";
import type { LegendItem } from "../core/VizLegend";

/**
 * The 'list' fleet style: a plain table of agents, one row each, in the
 * model's order (grouped by cluster). The only motion is the failing
 * flicker on a status dot; under reduced motion the dot is still and the
 * health word beside it says the same thing.
 */

export const FLEET_LIST_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Healthy" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, label: "Degraded: over 85% load" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failing" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle" },
  { glyph: "bar", color: HEALTH_COLOR.ok, label: "Bar: load, in the agent's health colour" },
];

function summarize(agents: FleetAgent[]): string {
  const failing = agents.filter((a) => a.health === "failing").length;
  const busy = agents.filter((a) => a.health !== "idle" && a.activeRuns > 0).length;
  return `${agents.length} agents, ${failing} failing, ${busy} busy`;
}

function HealthCell({ health, motion }: { health: Health; motion: FleetViewProps["motion"] }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className={cn(
          "size-2 shrink-0 rounded-full",
          health === "failing" && motion === "full" && MOTION_CLASS.flicker
        )}
        style={{ background: HEALTH_COLOR[health] }}
      />
      <span className="text-xs">{HEALTH_LABEL[health]}</span>
    </span>
  );
}

function LoadCell({ agent }: { agent: FleetAgent }) {
  const pct = Math.round(agent.load * 100);
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden className="bg-muted h-1.5 w-16 overflow-hidden rounded-sm">
        <span
          className="block h-full"
          style={{ width: `${pct}%`, background: HEALTH_COLOR[agent.health] }}
        />
      </span>
      <span className="font-mono text-xs tabular-nums">{pct}%</span>
    </span>
  );
}

export function FleetList({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const clusters = React.useMemo(
    () => new Map(snapshot.clusters.map((c) => [c.id, c])),
    [snapshot.clusters]
  );

  const columns: Column<FleetAgent>[] = [
    {
      id: "agent",
      header: "Agent",
      cell: (a) => (
        <span title={a.name} className="block max-w-56 truncate font-mono text-xs">
          {a.name}
        </span>
      ),
    },
    {
      id: "health",
      header: "Health",
      width: "w-28",
      cell: (a) => <HealthCell health={a.health} motion={motion} />,
    },
    { id: "load", header: "Load", width: "w-32", cell: (a) => <LoadCell agent={a} /> },
    {
      id: "runs",
      header: "Active runs",
      width: "w-24",
      align: "right",
      cell: (a) => <span className="font-mono text-xs tabular-nums">{a.activeRuns}</span>,
    },
    {
      id: "queued",
      header: "Queued",
      width: "w-20",
      align: "right",
      cell: (a) => <span className="font-mono text-xs tabular-nums">{a.queued}</span>,
    },
    {
      id: "cluster",
      header: "Cluster",
      cell: (a) => {
        const c = clusters.get(a.clusterId);
        const name = c?.name ?? a.clusterId;
        return (
          <span title={name} className="block max-w-48 truncate font-mono text-xs">
            {name}
          </span>
        );
      },
    },
  ];

  const activation = onSelectAgent
    ? {
        onRowActivate: (a: FleetAgent) => onSelectAgent(a.id),
        rowLabel: (a: FleetAgent) => `${a.name}, ${HEALTH_LABEL[a.health].toLowerCase()}`,
      }
    : {};

  return (
    // A group, not an img: an img role would hide the table and its row
    // buttons from assistive tech.
    <div
      role="group"
      aria-label={summarize(snapshot.agents)}
      data-motion={motion}
      className={className}
    >
      <DataTable
        label="Agents"
        controller={staticController(snapshot.agents)}
        columns={columns}
        getRowId={(a) => a.id}
        empty={{ icon: <BotIcon className="size-5" />, title: "No agents in this fleet" }}
        rowClassName={(a) => (a.id === selectedAgentId ? "bg-muted" : undefined)}
        {...activation}
      />
    </div>
  );
}
