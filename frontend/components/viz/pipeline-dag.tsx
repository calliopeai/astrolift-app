"use client";

import { Handle, Position, type Edge, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";
import * as React from "react";

import { cn } from "@/lib/utils";

import { FlowGraph } from "./flow-graph";
import { rankLayout } from "./flow-layout";

/** One node in a run graph — a pipeline job or a deploy-per-env stage. */
export interface PipelineDagStage extends Record<string, unknown> {
  /** Stable id referenced by other stages' `needs`. */
  id: string;
  name: string;
  /** Raw status string; mapped to a status token below. */
  status: string;
  /** Upstream stage ids this one depends on (→ incoming edges). */
  needs?: string[];
  startedAt?: string | null;
  finishedAt?: string | null;
  /** Optional click target. */
  href?: string;
}

// Collapse the many backend status spellings onto the #A1 status tokens.
type StageTone = "success" | "danger" | "running" | "pending" | "muted";

function toneFor(status: string): StageTone {
  const s = status.toLowerCase();
  if (["success", "succeeded", "passed", "complete", "completed"].includes(s)) return "success";
  if (["failure", "failed", "error", "errored", "timed_out"].includes(s)) return "danger";
  if (["running", "in_progress", "started", "active"].includes(s)) return "running";
  if (["pending", "queued", "waiting", "scheduled", "created"].includes(s)) return "pending";
  return "muted"; // cancelled, skipped, unknown
}

const TONE_RING: Record<StageTone, string> = {
  success: "ring-success bg-success/5",
  danger: "ring-danger-border bg-danger/5",
  running: "ring-info bg-info/5",
  pending: "ring-warning bg-warning/5",
  muted: "ring-muted-foreground/20 bg-muted/40",
};

const TONE_DOT: Record<StageTone, string> = {
  success: "bg-success",
  danger: "bg-danger",
  running: "bg-info animate-pulse",
  pending: "bg-warning",
  muted: "bg-muted-foreground/40",
};

function formatDuration(startedAt?: string | null, finishedAt?: string | null): string | null {
  if (!startedAt) return null;
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) return null;
  const end = finishedAt ? Date.parse(finishedAt) : NaN;
  if (Number.isNaN(end)) return "running"; // started, not yet finished
  const ms = Math.max(0, end - start);
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

function StageNode({ data }: NodeProps<Node<PipelineDagStage>>) {
  const tone = toneFor(data.status);
  const duration = formatDuration(data.startedAt, data.finishedAt);

  return (
    <div
      className={cn(
        "min-w-[168px] max-w-[220px] rounded-md border bg-background p-3 ring-1 shadow-sm",
        TONE_RING[tone],
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground/30" />
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground/30" />
      <div className="flex items-center gap-1.5">
        <span className={cn("size-1.5 shrink-0 rounded-full", TONE_DOT[tone])} />
        <div className="truncate text-sm font-medium leading-tight">{data.name}</div>
      </div>
      <div className="text-muted-foreground mt-1 flex items-center justify-between gap-2 text-2xs">
        <span className="capitalize">{data.status.replace(/_/g, " ")}</span>
        {duration && <span className="font-mono tabular-nums">{duration}</span>}
      </div>
    </div>
  );
}

const NODE_TYPES: NodeTypes = { stage: StageNode };

export interface PipelineDagProps {
  stages: PipelineDagStage[];
  height?: number | string;
  className?: string;
  onStageClick?: (stage: PipelineDagStage) => void;
}

/**
 * A pipeline / deployment run rendered as a DAG: each stage is a status-
 * coloured node with its duration; edges come from `needs`. Built on the
 * shared `FlowGraph`, so it inherits zoom / pan / the reduced-motion story.
 *
 * Stages with no `needs` form the roots (rank 0); the rest layer left-to-right
 * by dependency depth. Remount via a `key` (e.g. the run id) to re-flow when a
 * different run is selected.
 */
export function PipelineDag({ stages, height = 320, className, onStageClick }: PipelineDagProps) {
  const nodes = React.useMemo<Node<PipelineDagStage>[]>(() => {
    const ids = stages.map((s) => s.id);
    const edges = stages.flatMap((s) => (s.needs ?? []).map((dep) => ({ source: dep, target: s.id })));
    const pos = rankLayout(ids, edges);
    return stages.map((s) => ({
      id: s.id,
      type: "stage",
      position: pos.get(s.id) ?? { x: 0, y: 0 },
      data: s,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const edges = React.useMemo<Edge[]>(
    () =>
      stages.flatMap((s) =>
        (s.needs ?? [])
          .filter((dep) => stages.some((t) => t.id === dep))
          .map((dep) => ({ id: `${dep}->${s.id}`, source: dep, target: s.id })),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const handleClick = React.useCallback(
    (_id: string, data: unknown) => {
      const stage = data as PipelineDagStage;
      if (onStageClick) onStageClick(stage);
    },
    [onStageClick],
  );

  return (
    <FlowGraph
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      height={height}
      className={className}
      onNodeClick={handleClick}
      showMiniMap={false}
      fitViewOptions={{ padding: 0.25, maxZoom: 1.1 }}
    />
  );
}
