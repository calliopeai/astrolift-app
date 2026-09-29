"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import {
  useUpdateConfiguredWorkflow,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import {
  selectTriggers,
  triggerRows,
  type TriggerRow,
  WORKFLOW_TRIGGERS_LIST,
} from "./workflow-triggers-list";
import type { WorkflowTriggersViewProps } from "./WorkflowTriggers";

/**
 * Data and actions for the Triggers tab: the list state in the URL, the
 * workflow's triggers as rows (its one trigger today, from the frame's
 * workflow read; see workflow-triggers-list.ts), the `me.modules` manage
 * gate, and the enable/disable mutation with its toasts. `refetch` reloads
 * the frame's workflow after a change. No rows for a definition.
 */
export function useWorkflowTriggers(
  workflow: ConfiguredWorkflowWithRuns | null,
  refetch: () => void
): WorkflowTriggersViewProps {
  const list = useListState(WORKFLOW_TRIGGERS_LIST);
  const { state } = list;
  const entitlement = useWorkflowsEntitlement();
  const [updateWorkflow] = useUpdateConfiguredWorkflow();
  const [togglingId, setTogglingId] = useState<string | null>(null);

  const all = useMemo(() => triggerRows(workflow), [workflow]);
  const { rows, totalCount } = selectTriggers(all, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  // A trigger's Enabled is its workflow's switch: every row turns the workflow on or off.
  const onToggle = async (row: TriggerRow) => {
    if (!workflow) return;
    const next = !row.isEnabled;
    setTogglingId(row.id);
    const { data } = await updateWorkflow({
      variables: { slug: workflow.slug, isEnabled: next, orgId: workflow.organizationGuid },
    }).finally(() => setTogglingId(null));
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
    list,
    rows,
    totalCount,
    canManage: entitlement.canManage,
    togglingId,
    onToggle: (row) => void onToggle(row),
  };
}
