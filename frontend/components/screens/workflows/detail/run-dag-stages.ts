import type { PipelineDagStage } from "@/components/viz";
import type { WorkflowStage, WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

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
