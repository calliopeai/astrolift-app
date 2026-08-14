"use client";

import * as React from "react";

import { PipelineDag, type PipelineDagStage } from "@/components/viz";
import type {
  WorkflowDefinitionSummary,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";
import { cn } from "@/lib/utils";

export function formatWorkflowModel(model: string): string {
  const normalized = model.replace(/^(?:us|global)\.anthropic\./, "").replace(/^anthropic\./, "");
  const match = normalized.match(/^claude-(haiku|sonnet|opus)-(\d+)-(\d+)/i);
  if (!match) return normalized || "Platform default";
  const tier = match[1].charAt(0).toUpperCase() + match[1].slice(1).toLowerCase();
  return `Claude ${tier} ${match[2]}.${match[3]}`;
}

function stageLabel(stage: WorkflowTopologyStage): string {
  if (stage.kind === "workflow" && stage.workflowRef) {
    return `Workflow · ${stage.workflowRef}`;
  }
  const role = (stage.role || stage.kind).replace(/[_-]+/g, " ");
  const agent = stage.agentSlug || stage.agentRef;
  return agent ? `${role} · ${agent}` : role;
}

/**
 * Compact, shared workflow graph. Detailed environment/model/prompt/output
 * metadata belongs in the node inspector and workflow detail surface; project
 * dashboards only need topology, membership, and live state at a glance.
 */
export function WorkflowTopology({
  stages,
  className,
  height = 190,
  statusByOrder,
}: {
  stages: WorkflowTopologyStage[];
  className?: string;
  height?: number;
  statusByOrder?: Record<number, string>;
}) {
  const dagStages = React.useMemo<PipelineDagStage[]>(() => {
    const ordered = [...stages].sort((a, b) => a.order - b.order);
    return ordered.map((stage, index) => {
      const id = stage.guid;
      const row: PipelineDagStage = {
        id,
        name: stageLabel(stage),
        status: statusByOrder?.[stage.order] ?? "configured",
        needs: index > 0 ? [ordered[index - 1].guid] : [],
        href:
          stage.kind === "workflow" && stage.workflowRef
            ? `/workflows/${encodeURIComponent(stage.workflowRef)}/observe`
            : stage.agentSlug || stage.agentRef
              ? `/agents/${encodeURIComponent(stage.agentSlug || stage.agentRef)}/build`
              : undefined,
      };
      return row;
    });
  }, [stages, statusByOrder]);

  if (dagStages.length === 0) {
    return <p className="text-muted-foreground text-sm">No stages declared.</p>;
  }

  const signature = dagStages
    .map(
      (stage) =>
        `${stage.id}:${stage.name}:${stage.status}:${(stage.needs ?? []).join(",")}:${stage.topologyKind ?? "stage"}`
    )
    .join("|");
  return (
    <PipelineDag
      key={signature}
      stages={dagStages}
      height={height}
      className={cn("bg-muted/15", className)}
      variant={Object.keys(statusByOrder ?? {}).length > 0 ? "telemetry" : "default"}
      animateActiveEdges
      ariaLabel="Workflow stage topology"
    />
  );
}

/**
 * One graph for the whole project packet. Independent workflow roots form
 * compact lanes; referenced definitions expand inside the parent execution
 * path. This avoids stacking one large graph per definition while keeping
 * every workflow and agent a direct navigation target.
 */
export function ProjectWorkflowTopology({
  workflows,
  runStatusByWorkflow,
  stageStatusByWorkflow,
  className,
  height = 230,
}: {
  workflows: WorkflowDefinitionSummary[];
  runStatusByWorkflow?: Record<string, string>;
  stageStatusByWorkflow?: Record<string, Record<number, string>>;
  className?: string;
  height?: number;
}) {
  const dagStages = React.useMemo(
    () => buildProjectWorkflowDag(workflows, runStatusByWorkflow, stageStatusByWorkflow),
    [runStatusByWorkflow, stageStatusByWorkflow, workflows]
  );

  if (dagStages.length === 0) {
    return <p className="text-muted-foreground text-sm">No workflows declared.</p>;
  }

  const signature = dagStages
    .map(
      (stage) =>
        `${stage.id}:${stage.name}:${stage.status}:${(stage.needs ?? []).join(",")}:${stage.topologyKind ?? "stage"}`
    )
    .join("|");
  const hasRuntimeState = Object.keys(runStatusByWorkflow ?? {}).length > 0;
  return (
    <PipelineDag
      key={signature}
      stages={dagStages}
      height={height}
      className={cn("bg-muted/15", className)}
      variant={hasRuntimeState ? "telemetry" : "default"}
      animateActiveEdges
      ariaLabel="Project workflow topology"
    />
  );
}

/**
 * Expand nested definitions into the parent execution path. Referenced
 * definitions are not rendered again as peer roots, so a composed workflow
 * reads as one root cascading through its subflows and their agents.
 */
export function buildProjectWorkflowDag(
  workflows: WorkflowDefinitionSummary[],
  runStatusByWorkflow: Record<string, string> = {},
  stageStatusByWorkflow: Record<string, Record<number, string>> = {}
): PipelineDagStage[] {
  const bySlug = new Map(workflows.map((workflow) => [workflow.slug, workflow]));
  const referenced = new Set(
    workflows.flatMap((workflow) =>
      workflow.stages.flatMap((stage) =>
        stage.kind === "workflow" && bySlug.has(stage.workflowRef) ? [stage.workflowRef] : []
      )
    )
  );
  const roots = workflows.filter((workflow) => !referenced.has(workflow.slug));
  const entrypoints = roots.length > 0 ? roots : workflows;
  const rows: PipelineDagStage[] = [];

  const appendWorkflow = (
    workflow: WorkflowDefinitionSummary,
    needs: string[],
    path: string,
    ancestry: Set<string>,
    nested: boolean,
    fallbackStatus?: string
  ): string[] => {
    const rootId = `${path}:workflow:${workflow.guid}`;
    rows.push({
      id: rootId,
      name: workflow.name,
      status: runStatusByWorkflow[workflow.guid] ?? fallbackStatus ?? "configured",
      needs,
      href: `/workflows/${encodeURIComponent(workflow.slug)}/observe`,
      topologyKind: nested ? "subflow" : "workflow",
    });

    let previous = [rootId];
    const nextAncestry = new Set(ancestry).add(workflow.guid);
    for (const stage of [...workflow.stages].sort((a, b) => a.order - b.order)) {
      const status = stageStatusByWorkflow[workflow.guid]?.[stage.order] ?? "configured";
      const child = stage.kind === "workflow" ? bySlug.get(stage.workflowRef) : undefined;
      if (child && !nextAncestry.has(child.guid)) {
        previous = appendWorkflow(
          child,
          previous,
          `${path}:stage:${stage.guid}`,
          nextAncestry,
          true,
          status
        );
        continue;
      }

      const id = `${path}:stage:${stage.guid}`;
      rows.push({
        id,
        name: stageLabel(stage),
        status,
        needs: previous,
        href:
          stage.kind === "workflow" && stage.workflowRef
            ? `/workflows/${encodeURIComponent(stage.workflowRef)}/observe`
            : stage.agentSlug || stage.agentRef
              ? `/agents/${encodeURIComponent(stage.agentSlug || stage.agentRef)}/build`
              : undefined,
      });
      previous = [id];
    }
    return previous;
  };

  for (const workflow of entrypoints) {
    appendWorkflow(workflow, [], `root:${workflow.guid}`, new Set(), false);
  }
  return rows;
}
