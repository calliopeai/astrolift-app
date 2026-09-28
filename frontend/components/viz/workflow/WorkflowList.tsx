"use client";

import { WorkflowIcon } from "lucide-react";
import * as React from "react";

import { DataTable, type Column } from "@/components/data-table";
import { cn } from "@/lib/utils";

import { HEALTH_COLOR, MOTION_CLASS, type Health } from "../core/semantics";
import { staticController } from "../core/static-table";
import type { LegendItem } from "../core/VizLegend";
import type { TrainState, WorkflowTrain, WorkflowViewProps } from "../core/workflow-model";

/**
 * The 'list' workflow style: a plain table of runs with their line, the
 * station they are at, and their state. Held runs breathe and failed runs
 * flicker on the state dot; under reduced motion the dot is still and the
 * state word says the same thing.
 */

export const WORKFLOW_LIST_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Moving" },
  { glyph: "glow", color: HEALTH_COLOR.degraded, label: "Breathing: held at a gate" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failed" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Done" },
];

const STATE_HEALTH: Record<TrainState, Health> = {
  moving: "ok",
  held: "degraded",
  failed: "failing",
  done: "idle",
};

const STATE_LABEL: Record<TrainState, string> = {
  moving: "Moving",
  held: "Held",
  failed: "Failed",
  done: "Done",
};

const STATE_MOTION: Partial<Record<TrainState, string>> = {
  held: MOTION_CLASS.breathe,
  failed: MOTION_CLASS.flicker,
};

function formatAge(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ${m % 60}m ago`;
}

function summarize(trains: WorkflowTrain[], lines: number): string {
  const count = (s: TrainState) => trains.filter((t) => t.state === s).length;
  return (
    `${lines} workflows, ${trains.length} runs: ${count("moving")} moving, ` +
    `${count("held")} held at gates, ${count("failed")} failed, ${count("done")} done`
  );
}

export function WorkflowList({ snapshot, motion, onSelectRun, className }: WorkflowViewProps) {
  const lines = React.useMemo(
    () => new Map(snapshot.lines.map((l) => [l.id, l])),
    [snapshot.lines]
  );

  const columns: Column<WorkflowTrain>[] = [
    {
      id: "run",
      header: "Run",
      width: "w-24",
      cell: (t) => (
        <span title={t.label} className="block max-w-24 truncate font-mono text-xs">
          {t.label}
        </span>
      ),
    },
    {
      id: "line",
      header: "Workflow",
      cell: (t) => {
        const name = lines.get(t.lineId)?.name ?? t.lineId;
        return (
          <span title={name} className="block max-w-48 truncate text-xs">
            {name}
          </span>
        );
      },
    },
    {
      id: "station",
      header: "Station",
      cell: (t) => {
        const line = lines.get(t.lineId);
        const station = line?.stations[t.at];
        const name = station?.name ?? `#${t.at}`;
        const last = line ? line.stations.length - 1 : t.at;
        return (
          <span className="flex items-center gap-2 text-xs">
            <span title={name} className="max-w-36 truncate">
              {name}
            </span>
            <span className="text-muted-foreground font-mono tabular-nums">
              {t.at + 1}/{last + 1}
            </span>
            {station?.kind === "gate" && (
              <span className="text-muted-foreground text-2xs uppercase">gate</span>
            )}
          </span>
        );
      },
    },
    {
      id: "state",
      header: "State",
      width: "w-28",
      cell: (t) => {
        const health = STATE_HEALTH[t.state];
        return (
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className={cn(
                "size-2 shrink-0 rounded-full",
                motion === "full" && STATE_MOTION[t.state]
              )}
              style={{ background: HEALTH_COLOR[health] }}
            />
            <span className="text-xs">{STATE_LABEL[t.state]}</span>
          </span>
        );
      },
    },
    {
      id: "started",
      header: "Started",
      width: "w-28",
      align: "right",
      cell: (t) => (
        <span className="text-muted-foreground font-mono text-xs tabular-nums">
          {formatAge(snapshot.now - t.startedAt)}
        </span>
      ),
    },
  ];

  const activation = onSelectRun
    ? {
        onRowActivate: (t: WorkflowTrain) => onSelectRun(t.id),
        rowLabel: (t: WorkflowTrain) =>
          `${t.label}, ${lines.get(t.lineId)?.name ?? t.lineId}, ${STATE_LABEL[t.state].toLowerCase()}`,
      }
    : {};

  return (
    // A group, not an img: an img role would hide the table and its row
    // buttons from assistive tech.
    <div
      role="group"
      aria-label={summarize(snapshot.trains, snapshot.lines.length)}
      data-motion={motion}
      className={className}
    >
      <DataTable
        label="Runs"
        controller={staticController(snapshot.trains)}
        columns={columns}
        getRowId={(t) => t.id}
        empty={{ icon: <WorkflowIcon className="size-5" />, title: "No runs on any workflow" }}
        {...activation}
      />
    </div>
  );
}
