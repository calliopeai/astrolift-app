"use client";

import * as React from "react";

import type { PipelineDagStage } from "@/components/viz";
import { useWorkflowStageExecutions, useWorkflowStages } from "@/graphql/workflows/tiered.hooks";

import { buildRunDagStages } from "./run-dag-stages";

// 4s sits inside the ticket's 3–5s live window (#1090). The poll is gated on
// run terminality by the caller (pollInterval 0 once the run settles).
const POLL_MS = 4000;

/**
 * The data half of the live run flow (#1090): the planned stages and the
 * run's stage executions, merged into DAG nodes, plus the remount signature.
 *
 * `workflowId` / `runId` are the *Temporal* execution identity (from the run's
 * engine-instance detail), which is what `workflowStageExecutions` keys on —
 * not the tier-2 workflow guid. They arrive a beat after the run does (once
 * the engine detail resolves); until then the query skips and only the planned
 * skeleton shows.
 */
export function useWorkflowRunDag({
  workflowId,
  runId,
  definitionSlug,
  isTerminal,
}: {
  workflowId: string | null;
  runId: string | null;
  definitionSlug: string;
  isTerminal: boolean;
}) {
  const { stages, loading: stagesLoading } = useWorkflowStages(definitionSlug);
  const {
    executions,
    loading: execLoading,
    refetch,
  } = useWorkflowStageExecutions({
    workflowId,
    runId,
    pollInterval: isTerminal ? 0 : POLL_MS,
  });

  const dagStages = React.useMemo<PipelineDagStage[]>(
    () => buildRunDagStages(stages, executions),
    [stages, executions]
  );

  // Remount signature: any status change or new execution re-flows the graph.
  const signature = React.useMemo(
    () =>
      `${stages.length}|${executions
        .map((x) => `${x.guid}:${x.status}`)
        .sort()
        .join("|")}`,
    [stages.length, executions]
  );

  return {
    loading: stagesLoading || execLoading,
    dagStages,
    signature,
    executions,
    refetch,
  };
}
