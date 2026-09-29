"use client";

import { Loader2Icon } from "lucide-react";
import type * as React from "react";

import { PipelineDag, type PipelineDagStage } from "@/components/viz";

export interface WorkflowRunDagViewProps {
  loading: boolean;
  dagStages: PipelineDagStage[];
  /** Remount key: any status change or new execution re-flows the graph. */
  signature: string;
  /** The run's pending human gates, under the graph. */
  gateReview?: React.ReactNode;
}

/**
 * Live run flow for a single workflow run (#1090). Overlays run state on the
 * stage DAG: the planned stages render as a pending skeleton, executions light
 * them up (active glow + pulse, completed settle), and fan-out children spawn
 * as new nodes. `PipelineDag` reads its graph once on mount, so a signature
 * `key` remounts it to re-flow on every status change / new execution. Motion
 * (glow, pulse, edge flow) is `variant="telemetry"`; reduced-motion falls back
 * to static status colours via the globals.css media query.
 */
export function WorkflowRunDagView({
  loading,
  dagStages,
  signature,
  gateReview,
}: WorkflowRunDagViewProps) {
  if (loading && dagStages.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-md border p-8">
        <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
      </div>
    );
  }

  if (dagStages.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed p-6 text-center text-xs">
        No stage activity to graph for this run yet.
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <PipelineDag
        key={signature}
        stages={dagStages}
        height={280}
        variant="telemetry"
        animateActiveEdges
      />
      {gateReview}
    </div>
  );
}
