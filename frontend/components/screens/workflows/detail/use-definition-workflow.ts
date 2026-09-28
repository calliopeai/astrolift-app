"use client";

import { useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  useRunWorkflowDefinition,
  useWorkflowDefinitionRuns,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import {
  useCancelWorkflowInstance,
  useWorkflowInstanceDetail,
} from "@/graphql/workflows/workflows.hooks";

const RUNS_POLL_MS = 4000;
const TERMINAL = new Set(["completed", "failed", "cancelled", "terminated", "timed_out"]);

function isTerminal(run: WorkflowDefinitionRun | null): boolean {
  return !run || TERMINAL.has(run.status.toLowerCase());
}

/** This definition's runs, newest first, polled while the page is open. */
function useDefinitionRuns(definition: WorkflowDefinitionSummary) {
  const query = useWorkflowDefinitionRuns({
    orgId: definition.organizationGuid,
    projectId: definition.projectGuid,
    limit: 100,
    pollInterval: RUNS_POLL_MS,
  });
  const runs = React.useMemo(
    () =>
      query.runs
        .filter((run) => run.definitionGuid === definition.guid)
        .sort((a, b) => ((a.startedAt ?? "") < (b.startedAt ?? "") ? 1 : -1)),
    [definition.guid, query.runs]
  );
  return { ...query, runs };
}

/** Run pillar: dispatch now (gated `canRun`) and the latest run. */
export function useDefinitionRun(definition: WorkflowDefinitionSummary) {
  const entitlement = useWorkflowsEntitlement();
  const [runWorkflow, { loading: running }] = useRunWorkflowDefinition();
  const { runs, refetch } = useDefinitionRuns(definition);

  const run = async (): Promise<boolean> => {
    const { data } = await runWorkflow({
      variables: { workflowSlug: definition.slug, triggerPayload: null },
    });
    if (data?.runWorkflowDefinition?.ok) {
      toast.success("Workflow started", {
        description: data.runWorkflowDefinition.temporalWorkflowId ?? definition.name,
      });
      await refetch();
      return true;
    }
    const message = data?.runWorkflowDefinition?.errors?.[0]?.messages?.[0];
    toast.error(message ?? "Failed to start workflow");
    return false;
  };

  const latest = (runs[0] ?? null) as WorkflowDefinitionRun | null;
  return { canRun: entitlement.canRun, running, latest, run };
}

/** Observe pillar: the run list and the run the URL asks for (`?run=`). */
export function useDefinitionObserve(definition: WorkflowDefinitionSummary) {
  const { runs, loading, error, refetch } = useDefinitionRuns(definition);
  const requestedRunGuid = useSearchParams().get("run");
  return { runs, loading, error, refetch, requestedRunGuid };
}

/** One run's engine timeline and cancel (gated `canRun`). */
export function useDefinitionRunPanel(run: WorkflowDefinitionRun | null, onRefresh: () => unknown) {
  const entitlement = useWorkflowsEntitlement();
  const detail = useWorkflowInstanceDetail(run?.temporalWorkflowId ?? null);
  const [cancelRun, { loading: cancelling }] = useCancelWorkflowInstance();

  const cancel = async (): Promise<boolean> => {
    if (!run) return false;
    const { data } = await cancelRun({ variables: { workflowId: run.temporalWorkflowId } });
    if (data?.cancelWorkflowInstance?.ok) {
      toast.success("Cancellation requested");
      await onRefresh();
      return true;
    }
    toast.error(data?.cancelWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Cancel failed");
    return false;
  };

  return {
    canRun: entitlement.canRun,
    terminal: isTerminal(run),
    detail: detail.detail,
    detailLoading: detail.loading,
    detailError: detail.error ?? null,
    cancelling,
    cancel,
  };
}
