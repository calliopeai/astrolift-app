"use client";

import { AlertTriangleIcon, WorkflowIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import { formatTriggerKind } from "./workflow-run-state";

export interface WorkflowDetailShellViewProps {
  workflowSlug: string;
  /** The resolved configured workflow, or null while loading / when not found. */
  workflow: ConfiguredWorkflowWithRuns | null;
  loading: boolean;
  error?: { message: string } | null;
  /** The BROCS pillar bar (`WorkflowTabs`). */
  tabs?: React.ReactNode;
  /** The active tab's content, shown once the workflow has resolved. */
  children?: React.ReactNode;
}

/**
 * Shared chrome for every `/workflows/[slug]/<pillar>` page: the identity
 * header (name · enabled · trigger · definition), the pillar bar slot, then
 * the active tab's content. Mirrors `AgentDetailShellView`.
 */
export function WorkflowDetailShellView({
  workflowSlug,
  workflow,
  loading,
  error,
  tabs,
  children,
}: WorkflowDetailShellViewProps) {
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
      {tabs}
      {children}
    </PageShell>
  );
}
