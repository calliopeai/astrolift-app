"use client";

import { WorkflowIcon } from "lucide-react";
import * as React from "react";

import { DataTable, type Column } from "@/components/data-table";
import { cn } from "@/lib/utils";

import { HEALTH_COLOR, MOTION_CLASS, type Health } from "../core/semantics";
import { staticController } from "../core/static-table";
import { WORKFLOW_SHAPE_LEGEND, type LegendItem } from "../core/VizLegend";
import type {
  TrainState,
  WorkflowLine,
  WorkflowTrain,
  WorkflowViewProps,
} from "../core/workflow-model";

import { runRound } from "./workflow-graph-layout";
import { STATE_WORD_HEALTH, workflowRows, type WorkflowRow } from "./workflow-list-rows";

/**
 * The 'list' workflow style, table first: one row per stage of every
 * workflow with its kind, state, round against its bound, attempts and
 * branch or sub-task progress, and each loop on its own row in words ("Test
 * failed goes back to Code, round 3 of 5"). Under it, every run with its line,
 * station, round and state.
 *
 * Held runs breathe and failed runs flicker on the state dot; under reduced
 * motion the dot is still and the state word says the same thing.
 */

export const WORKFLOW_LIST_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Moving" },
  { glyph: "glow", color: HEALTH_COLOR.degraded, label: "Breathing: held at a gate" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failed" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Done" },
  {
    ...WORKFLOW_SHAPE_LEGEND.returnTrack,
    label: "Loop row: where work goes back and its bound, amber while a run is sent back",
  },
  {
    ...WORKFLOW_SHAPE_LEGEND.roundNearBound,
    label: "Round in amber: one round from the bound; past it the run fails",
  },
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

/** Only waiting breathes and only failure flickers; every other state holds still. */
const WORD_MOTION: Record<string, string> = {
  Held: MOTION_CLASS.breathe,
  "Waiting for approval": MOTION_CLASS.breathe,
  Failed: MOTION_CLASS.flicker,
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
  const sent = trains.filter((t) => t.onLoopId).length;
  return (
    `${lines} ${lines === 1 ? "workflow" : "workflows"}, ${trains.length} ${trains.length === 1 ? "run" : "runs"}: ${count("moving")} moving, ` +
    `${count("held")} held at gates, ${count("failed")} failed, ${count("done")} done` +
    (sent ? `, ${sent} sent back along a loop` : "")
  );
}

function StateDot({
  health,
  word,
  motion,
}: {
  health: Health;
  word: string;
  motion: "full" | "reduced";
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className={cn("size-2 shrink-0 rounded-full", motion === "full" && WORD_MOTION[word])}
        style={{ background: HEALTH_COLOR[health] }}
      />
      <span className="text-xs">{word}</span>
    </span>
  );
}

function Count({ value, near }: { value: string | null; near?: boolean }) {
  if (!value) return <span className="sr-only">none</span>;
  return (
    <span
      className="font-mono text-xs tabular-nums"
      style={near ? { color: HEALTH_COLOR.degraded } : undefined}
    >
      {value}
      {near && <span className="sr-only">, one round from the bound</span>}
    </span>
  );
}

/** "Sent back to Code" for a run on a return track, else the station it is at. */
function where(line: WorkflowLine | undefined, t: WorkflowTrain): string {
  const loop = t.onLoopId ? line?.loops?.find((l) => l.id === t.onLoopId) : undefined;
  if (loop && line) {
    const to = line.stations[loop.to]?.name ?? "stage";
    return loop.kind === "retry" ? `Retrying ${to}` : `Sent back to ${to}`;
  }
  return line?.stations[t.at]?.name ?? `#${t.at}`;
}

export function WorkflowList({
  snapshot,
  motion,
  onSelectRun,
  onSelectStation,
  className,
}: WorkflowViewProps) {
  const lines = React.useMemo(
    () => new Map(snapshot.lines.map((l) => [l.id, l])),
    [snapshot.lines]
  );
  const rows = React.useMemo(() => workflowRows(snapshot), [snapshot]);

  const stageColumns: Column<WorkflowRow>[] = [
    {
      id: "stage",
      header: "Stage",
      cell: (r) =>
        r.type === "loop" ? (
          <span className="flex items-start gap-1.5 pl-4 text-xs">
            <span aria-hidden className="text-muted-foreground">
              ↩
            </span>
            <span style={r.runs.length ? { color: HEALTH_COLOR.degraded } : undefined}>
              {r.words}
            </span>
          </span>
        ) : (
          <span title={r.station.name} className="block max-w-40 truncate text-xs font-medium">
            {r.station.name}
          </span>
        ),
    },
    {
      id: "workflow",
      header: "Workflow",
      cell: (r) => (
        <span title={r.workflow} className="text-muted-foreground block max-w-40 truncate text-xs">
          {r.workflow}
        </span>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      width: "w-28",
      cell: (r) => <span className="text-muted-foreground text-xs">{r.kind}</span>,
    },
    {
      id: "state",
      header: "State",
      width: "w-40",
      cell: (r) => <StateDot health={STATE_WORD_HEALTH[r.state]} word={r.state} motion={motion} />,
    },
    {
      id: "round",
      header: "Round",
      width: "w-20",
      cell: (r) => <Count value={r.round?.text ?? null} near={r.round?.near} />,
    },
    {
      id: "attempts",
      header: "Attempts",
      width: "w-20",
      cell: (r) => <Count value={r.type === "stage" ? r.attempts : null} />,
    },
    {
      id: "progress",
      header: "Progress",
      cell: (r) =>
        r.type === "stage" && r.progress ? (
          <span className="font-mono text-xs tabular-nums">{r.progress}</span>
        ) : (
          <span className="sr-only">none</span>
        ),
    },
    {
      id: "runs",
      header: "Runs",
      width: "w-32",
      cell: (r) =>
        r.runs.length ? (
          <span
            title={r.runs.map((t) => t.label).join(", ")}
            className="block max-w-32 truncate font-mono text-xs"
          >
            {r.runs.map((t) => t.label).join(", ")}
          </span>
        ) : (
          <span className="text-muted-foreground font-mono text-xs">0</span>
        ),
    },
  ];

  const stationOf = (r: WorkflowRow) =>
    r.type === "stage" ? r.station : r.line.stations[r.loop.from];
  const stageActivation =
    onSelectStation || onSelectRun
      ? {
          onRowActivate: (r: WorkflowRow) => {
            const st = stationOf(r);
            if (onSelectStation && st) onSelectStation(r.line.id, st.id);
            else if (r.runs[0]) onSelectRun?.(r.runs[0].id);
          },
          rowLabel: (r: WorkflowRow) =>
            r.type === "loop"
              ? `${r.workflow}: ${r.words}, ${r.state.toLowerCase()}`
              : [
                  `${r.station.name}, ${r.kind.toLowerCase()} in ${r.workflow}`,
                  r.round ? `round ${r.round.text.replace(" / ", " of ")}` : null,
                  r.attempts ? `attempt ${r.attempts.replace(" / ", " of ")}` : null,
                  r.progress,
                  r.state.toLowerCase(),
                ]
                  .filter(Boolean)
                  .join(", "),
        }
      : {};

  const runColumns: Column<WorkflowTrain>[] = [
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
        const name = where(line, t);
        const last = line ? line.stations.length - 1 : t.at;
        return (
          <span className="flex items-center gap-2 text-xs">
            <span
              title={name}
              className="max-w-36 truncate"
              style={t.onLoopId ? { color: HEALTH_COLOR.degraded } : undefined}
            >
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
      id: "round",
      header: "Round",
      width: "w-28",
      cell: (t) => {
        const line = lines.get(t.lineId);
        const r = line ? runRound(line, t) : null;
        return <Count value={r ? r.text : null} near={r?.near} />;
      },
    },
    {
      id: "state",
      header: "State",
      width: "w-28",
      cell: (t) =>
        t.onLoopId && t.state === "moving" ? (
          <StateDot health="degraded" word="Sent back" motion={motion} />
        ) : (
          <StateDot health={STATE_HEALTH[t.state]} word={STATE_LABEL[t.state]} motion={motion} />
        ),
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

  const runActivation = onSelectRun
    ? {
        onRowActivate: (t: WorkflowTrain) => onSelectRun(t.id),
        rowLabel: (t: WorkflowTrain) => {
          const line = lines.get(t.lineId);
          const r = line ? runRound(line, t) : null;
          const state =
            t.onLoopId && t.state === "moving"
              ? where(line, t).toLowerCase()
              : STATE_LABEL[t.state].toLowerCase();
          return [t.label, line?.name ?? t.lineId, r?.text, state].filter(Boolean).join(", ");
        },
      }
    : {};

  return (
    // A group, not an img: an img role would hide the tables and their row
    // buttons from assistive tech.
    <div
      role="group"
      aria-label={summarize(snapshot.trains, snapshot.lines.length)}
      data-motion={motion}
      className={cn("flex flex-col gap-4", className)}
    >
      <DataTable
        label="Stages"
        controller={staticController(rows)}
        columns={stageColumns}
        getRowId={(r) => `${r.type}:${r.id}`}
        empty={{ icon: <WorkflowIcon className="size-5" />, title: "No workflows" }}
        {...stageActivation}
      />
      <DataTable
        label="Runs"
        controller={staticController(snapshot.trains)}
        columns={runColumns}
        getRowId={(t) => t.id}
        empty={{ icon: <WorkflowIcon className="size-5" />, title: "No runs on any workflow" }}
        {...runActivation}
      />
    </div>
  );
}
