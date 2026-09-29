"use client";

import { toast } from "sonner";

import {
  useRunWorkflow,
  useUpdateConfiguredWorkflow,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import { latestRun } from "./workflow-run-state";

/**
 * Data and actions for a workflow's Run pillar: the `me.modules` gates, the
 * dispatch-now and enable/disable mutations with their toasts, and the latest
 * run. `refetch` reloads the shell's workflow after a change.
 */
export function useWorkflowRun(workflow: ConfiguredWorkflowWithRuns, refetch: () => void) {
  const entitlement = useWorkflowsEntitlement();
  const [runWorkflow, { loading: running }] = useRunWorkflow();
  const [updateWorkflow, { loading: toggling }] = useUpdateConfiguredWorkflow();

  const onRun = async () => {
    const { data } = await runWorkflow({
      variables: { workflowId: workflow.guid, orgId: workflow.organizationGuid },
    });
    if (data?.runWorkflow?.ok) {
      toast.success("Run started", {
        description: data.runWorkflow.workflowRunId
          ? `Run ${data.runWorkflow.workflowRunId}`
          : workflow.name,
      });
      refetch();
    } else {
      const errors = data?.runWorkflow?.errors ?? [];
      if (errors.length > 0) {
        for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
      } else {
        toast.error("Failed to start run");
      }
    }
  };

  const onToggle = async () => {
    const next = !workflow.isEnabled;
    const { data } = await updateWorkflow({
      variables: { slug: workflow.slug, isEnabled: next, orgId: workflow.organizationGuid },
    });
    if (data?.updateWorkflow?.ok) {
      toast.success(next ? "Workflow enabled" : "Workflow disabled");
      refetch();
    } else {
      const errors = data?.updateWorkflow?.errors ?? [];
      if (errors.length > 0) {
        for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
      } else {
        toast.error("Failed to update workflow");
      }
    }
  };

  return {
    workflow,
    latest: latestRun(workflow.runs),
    canRun: entitlement.canRun,
    canManage: entitlement.canManage,
    running,
    toggling,
    onRun,
    onToggle,
  };
}

export type WorkflowRunState = ReturnType<typeof useWorkflowRun>;
