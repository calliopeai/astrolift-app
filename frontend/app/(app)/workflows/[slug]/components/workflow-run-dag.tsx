"use client";

import { Loader2Icon } from "lucide-react";
import * as React from "react";

import { PipelineDag, type PipelineDagStage } from "@/components/viz";
import { useWorkflowStageExecutions, useWorkflowStages } from "@/graphql/workflows/tiered.hooks";
import type { WorkflowStage, WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

// 4s sits inside the ticket's 3–5s live window (#1090). The poll is gated on
// run terminality by the caller (pollInterval 0 once the run settles).
const POLL_MS = 4000;

function humanizeKind(kind: string): string {
  if (!kind) return "Stage";
  return kind.charAt(0).toUpperCase() + kind.slice(1).replace(/_/g, " ");
}

/**
 * Merge the planned stages (skeleton) with the run's stage executions (live
 * overlay) into a ranked DAG.
 *
 * Each `order` is a rank. A stage with no execution yet is a single `pending`
 * node (fan-out stages annotate their planned width as `×N`); a stage that has
 * spawned executions renders one node per execution, so fan-out children
 * appear as they spawn. Edges chain every rank to the previous populated rank,
 * so fan-out (1→N) and aggregation (N→1) read naturally.
 */
export function buildRunDagStages(
  stages: WorkflowStage[],
  executions: WorkflowStageExecution[]
): PipelineDagStage[] {
  const execByOrder = new Map<number, WorkflowStageExecution[]>();
  for (const x of executions) {
    const list = execByOrder.get(x.stageOrder) ?? [];
    list.push(x);
    execByOrder.set(x.stageOrder, list);
  }
  const stageByOrder = new Map<number, WorkflowStage>();
  for (const s of stages) stageByOrder.set(s.order, s);

  const orders = Array.from(new Set([...stageByOrder.keys(), ...execByOrder.keys()])).sort(
    (a, b) => a - b
  );

  const nodes: PipelineDagStage[] = [];
  let prevRankIds: string[] = [];
  for (const order of orders) {
    const def = stageByOrder.get(order) ?? null;
    const xs = [...(execByOrder.get(order) ?? [])].sort((a, b) =>
      a.executionId !== b.executionId
        ? a.executionId < b.executionId
          ? -1
          : 1
        : a.guid < b.guid
          ? -1
          : 1
    );
    const label =
      def?.agentDefinitionName ||
      (def?.kind === "workflow" && def.workflowRef
        ? `Workflow · ${def.workflowRef}`
        : humanizeKind(def?.kind ?? xs[0]?.stageKind ?? ""));
    const curIds: string[] = [];

    if (xs.length > 0) {
      xs.forEach((x, i) => {
        const id = `x:${x.guid}`;
        curIds.push(id);
        nodes.push({
          id,
          name: xs.length > 1 ? `${label} #${i + 1}` : label,
          status: x.status,
          needs: prevRankIds,
          startedAt: x.startedAt,
          finishedAt: x.endedAt,
          href:
            x.childWorkflowDefinitionSlug || def?.workflowRef
              ? `/workflows/${encodeURIComponent(
                  x.childWorkflowDefinitionSlug || def?.workflowRef || ""
                )}/observe${x.childWorkflowRunGuid ? `?run=${encodeURIComponent(x.childWorkflowRunGuid)}` : ""}`
              : undefined,
        });
      });
    } else if (def) {
      const fanned = def.fanOutCount != null && def.fanOutCount > 1;
      const id = `s:${def.guid}`;
      curIds.push(id);
      nodes.push({
        id,
        name: fanned ? `${label} ×${def.fanOutCount}` : label,
        status: "pending",
        needs: prevRankIds,
        href:
          def.kind === "workflow" && def.workflowRef
            ? `/workflows/${encodeURIComponent(def.workflowRef)}/observe`
            : undefined,
      });
    }

    if (curIds.length > 0) prevRankIds = curIds;
  }
  return nodes;
}

/**
 * Live run flow for a single workflow run (#1090). Overlays run state on the
 * stage DAG: the planned stages render as a pending skeleton, executions light
 * them up (active glow + pulse, completed settle), and fan-out children spawn
 * as new nodes. `PipelineDag` reads its graph once on mount, so a signature
 * `key` remounts it to re-flow on every status change / new execution. Motion
 * (glow, pulse, edge flow) is `variant="telemetry"`; reduced-motion falls back
 * to static status colours via the globals.css media query.
 *
 * `workflowId` / `runId` are the *Temporal* execution identity (from the run's
 * engine-instance detail), which is what `workflowStageExecutions` keys on —
 * not the tier-2 workflow guid. They arrive a beat after the run does (once
 * the engine detail resolves); until then the query skips and only the planned
 * skeleton shows.
 */
export function WorkflowRunDag({
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
  const { executions, loading: execLoading } = useWorkflowStageExecutions({
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

  if ((stagesLoading || execLoading) && dagStages.length === 0) {
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
    <PipelineDag
      key={signature}
      stages={dagStages}
      height={280}
      variant="telemetry"
      animateActiveEdges
    />
  );
}
