"use client";

import { RunTimelinePanelView } from "@/components/screens/workflows/detail/RunTimelinePanel";
import { useWorkflowObserve } from "@/components/screens/workflows/detail/use-workflow-observe";
import { WorkflowObserveView } from "@/components/screens/workflows/detail/WorkflowObserve";
import type {
  ConfiguredWorkflowWithRuns,
  TieredWorkflowRun,
} from "@/graphql/workflows/tiered.types";
import { useWorkflowInstanceDetail } from "@/graphql/workflows/workflows.hooks";

import { WorkflowRunDag } from "./workflow-run-dag";

/**
 * Observe pillar — the workflow's run history (tier-3 `workflowRuns`), with a
 * drill-down into the run's Temporal instance timeline. The timeline reuses
 * the instances-panel hook (`useWorkflowInstanceDetail`) so both surfaces
 * read the same engine history. The views live in
 * components/screens/workflows/detail/.
 */
export function ObserveContent({ workflow }: { workflow: ConfiguredWorkflowWithRuns }) {
  const state = useWorkflowObserve(workflow);

  return (
    <WorkflowObserveView
      {...state}
      timeline={
        <RunTimelinePanel
          run={state.selected}
          definitionSlug={workflow.definitionSlug}
          isTerminal={state.focusTerminal}
          onClose={() => state.onSelect(null)}
        />
      }
    />
  );
}

function RunTimelinePanel({
  run,
  definitionSlug,
  isTerminal,
  onClose,
}: {
  run: TieredWorkflowRun | null;
  definitionSlug: string;
  isTerminal: boolean;
  onClose: () => void;
}) {
  // The hook skips when the id is null (no run selected, or the run never
  // reached the engine), so it's safe to call unconditionally.
  const { detail, loading, error } = useWorkflowInstanceDetail(run?.temporalWorkflowId ?? null);

  return (
    <RunTimelinePanelView
      run={run}
      detail={detail}
      loading={loading}
      error={error}
      onClose={onClose}
      dag={
        run ? (
          <WorkflowRunDag
            key={run.guid}
            workflowId={run.temporalWorkflowId ?? detail?.instance.workflowId ?? null}
            runId={run.temporalRunId ?? detail?.instance.runId ?? null}
            definitionSlug={definitionSlug}
            isTerminal={isTerminal}
          />
        ) : null
      }
    />
  );
}
