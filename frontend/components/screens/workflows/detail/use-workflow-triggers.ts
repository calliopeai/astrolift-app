"use client";

import { toast } from "sonner";

import {
  useUpdateConfiguredWorkflow,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import type { WorkflowTriggersViewProps } from "./WorkflowTriggers";

/**
 * Data and actions for the Triggers tab: the trigger, the `me.modules`
 * manage gate, and the enable/disable mutation with its toasts. `refetch`
 * reloads the frame's workflow after a change. Null for a definition.
 */
export function useWorkflowTriggers(
  workflow: ConfiguredWorkflowWithRuns | null,
  refetch: () => void
): WorkflowTriggersViewProps {
  const entitlement = useWorkflowsEntitlement();
  const [updateWorkflow, { loading: toggling }] = useUpdateConfiguredWorkflow();

  const onToggle = async () => {
    if (!workflow) return;
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
    trigger: workflow,
    canManage: entitlement.canManage,
    toggling,
    onToggle: () => void onToggle(),
  };
}
