"use client";

import { AlertTriangleIcon, WorkflowIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useTieredWorkflow } from "@/graphql/workflows/tiered.hooks";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import { WorkflowTabs } from "./workflow-tabs";

// triggerKind is a free String! on the schema — label the known kinds and
// title-case anything else so unknown values degrade gracefully.
const TRIGGER_LABELS: Record<string, string> = {
  manual: "Manual",
  schedule: "Schedule",
  webhook: "Webhook",
};

export function formatTriggerKind(triggerKind: string): string {
  const key = triggerKind.toLowerCase();
  if (TRIGGER_LABELS[key]) return TRIGGER_LABELS[key];
  if (!triggerKind) return "—";
  return triggerKind.charAt(0).toUpperCase() + triggerKind.slice(1).toLowerCase();
}

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
 * tier-2 configured Workflow, renders the identity header (name · enabled ·
 * trigger · definition) and the BROCS `WorkflowTabs`, then slots the active
 * tab's content. Mirrors `AgentDetailShell` as a render-prop shell so each
 * pillar route owns only its own content.
 */
export function WorkflowDetailShell({ workflowSlug, children }: WorkflowDetailShellProps) {
  const { workflow, loading, error, refetch } = useTieredWorkflow(workflowSlug);

  if (loading && !workflow) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (error || !workflow) {
    return (
      <PageShell title="Workflow not found">
        <EmptyState
          icon={
            error ? <AlertTriangleIcon className="size-5" /> : <WorkflowIcon className="size-5" />
          }
          title={error ? "Couldn't load this workflow" : `No workflow with slug ${workflowSlug}`}
          description={
            error
              ? error.message
              : "It may have been deleted, belong to a different organization, or you may not have permission to view it."
          }
          actionHref="/workflows"
          actionLabel="Back to workflows"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <WorkflowIcon className="text-muted-foreground size-5" />
          </span>
          <span>{workflow.name}</span>
          <Badge variant={workflow.isEnabled ? "default" : "secondary"}>
            {workflow.isEnabled ? "Enabled" : "Disabled"}
          </Badge>
          <Badge variant="outline">{formatTriggerKind(workflow.triggerKind)}</Badge>
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground font-mono text-xs">{workflow.slug}</span>
          <span className="text-muted-foreground text-xs">
            definition {workflow.definitionName}
          </span>
          <span className="text-muted-foreground text-xs">
            {workflow.runCount === 1 ? "1 run" : `${workflow.runCount} runs`}
          </span>
        </span>
      }
    >
      <WorkflowTabs workflowSlug={workflow.slug} />
      {children({ workflow, refetch })}
    </PageShell>
  );
}
