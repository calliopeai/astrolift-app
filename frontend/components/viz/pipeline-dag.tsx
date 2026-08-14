"use client";

import {
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import Link from "next/link";
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

  const body = (
    <>
      <Handle
        type="target"
        position={Position.Left}
        className="!border-background !bg-muted-foreground/50 !-left-1.5 !size-2.5 !border-2"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!border-background !bg-muted-foreground/50 !-right-1.5 !size-2.5 !border-2"
      />
      <div className="flex min-w-0 items-center gap-1.5">
        <span className={cn("viz-node-dot size-1.5 shrink-0 rounded-full", TONE_DOT[tone])} />
        <div className="min-w-0 truncate text-sm leading-tight font-medium">{data.name}</div>
      </div>
      <div className="text-muted-foreground text-2xs mt-1 flex items-center justify-between gap-2">
        <span className="capitalize">{data.status.replace(/_/g, " ")}</span>
        {duration && <span className="font-mono tabular-nums">{duration}</span>}
      </div>
    </>
  );
  const className = cn(
    "viz-node relative flex h-16 w-56 flex-col justify-center overflow-visible rounded-md border bg-background px-3 py-2 ring-1 shadow-sm",
    data.href &&
      "cursor-pointer hover:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
    TONE_RING[tone]
  );
  return data.href ? (
    <Link href={data.href} className={className} title={`Open ${data.name}`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

const NODE_TYPES: NodeTypes = { stage: StageNode };

export interface PipelineDagProps {
  stages: PipelineDagStage[];
  height?: number | string;
  className?: string;
  onStageClick?: (stage: PipelineDagStage) => void;
  /** Visual treatment — forwarded to `FlowGraph` (#1055). */
  variant?: "default" | "telemetry";
  /**
   * Mark edges flowing into a `running` stage as `animated` so the telemetry
   * scope lights them with the live teal flow (#1090). Off by default, so
   * static run graphs (deployment/pipeline) are unaffected.
   */
  animateActiveEdges?: boolean;
  ariaLabel?: string;
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
export function PipelineDag({
  stages,
  height = 320,
  className,
  onStageClick,
  variant,
  animateActiveEdges,
  ariaLabel,
}: PipelineDagProps) {
  const nodes = React.useMemo<Node<PipelineDagStage>[]>(() => {
    const ids = stages.map((s) => s.id);
    const edges = stages.flatMap((s) =>
      (s.needs ?? []).map((dep) => ({ source: dep, target: s.id }))
    );
    const pos = rankLayout(ids, edges, { colWidth: 304, rowHeight: 96 });
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
          .map((dep) => ({
            id: `${dep}->${s.id}`,
            source: dep,
            target: s.id,
            type: "smoothstep",
            animated: !!animateActiveEdges && toneFor(s.status) === "running",
          }))
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const handleClick = React.useCallback(
    (_id: string, data: unknown) => {
      const stage = data as PipelineDagStage;
      if (onStageClick) onStageClick(stage);
    },
    [onStageClick]
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
      variant={variant}
      ariaLabel={ariaLabel}
    />
  );
}
