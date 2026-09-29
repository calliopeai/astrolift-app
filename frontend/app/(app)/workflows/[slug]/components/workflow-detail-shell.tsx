"use client";

import * as React from "react";

import { WorkflowDetailShellView } from "@/components/screens/workflows/detail/WorkflowDetailShell";
import { useTieredWorkflow } from "@/graphql/workflows/tiered.hooks";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import { WorkflowTabs } from "./workflow-tabs";

export { formatTriggerKind } from "@/components/screens/workflows/detail/workflow-run-state";

export interface WorkflowDetailRenderProps {
  workflow: ConfiguredWorkflowWithRuns;
  refetch: () => void;
}

interface WorkflowDetailShellProps {
  workflowSlug: string;
  /** Renders the active tab's content once the workflow has resolved. */
  children: (props: WorkflowDetailRenderProps) => React.ReactNode;
}

/**
 * Shared chrome for every `/workflows/[slug]/<pillar>` page: resolves the
 * tier-2 configured Workflow, renders the identity header and the BROCS
 * `WorkflowTabs`, then slots the active tab's content. Mirrors
 * `AgentDetailShell` as a render-prop shell so each pillar route owns only its
 * own content. The view lives in components/screens/workflows/detail/WorkflowDetailShell.
 */
export function WorkflowDetailShell({ workflowSlug, children }: WorkflowDetailShellProps) {
  const { workflow, loading, error, refetch } = useTieredWorkflow(workflowSlug);
  const resolved = !error && workflow ? workflow : null;

  return (
    <WorkflowDetailShellView
      workflowSlug={workflowSlug}
      workflow={workflow}
      loading={loading}
      error={error}
      tabs={resolved ? <WorkflowTabs workflowSlug={resolved.slug} /> : null}
    >
      {resolved ? children({ workflow: resolved, refetch }) : null}
    </WorkflowDetailShellView>
  );
}
