"use client";

import { PageShell } from "@/components/PageShell";
import {
  GitOpsCommitTimeline,
  WorkflowTimeline,
  type GitOpsCommit,
  type WorkflowActivity,
  type WorkflowRunSummary,
} from "@/components/observability";

export type PlaygroundObservabilityScreenProps = {
  run: WorkflowRunSummary;
  activities: WorkflowActivity[];
  commits: GitOpsCommit[];
  onCancel: () => void;
  onRetryActivity: (id: string) => void;
};

/** Demo of the workflow and GitOps timelines against a fixed dataset. */
export function PlaygroundObservabilityScreen({
  run,
  activities,
  commits,
  onCancel,
  onRetryActivity,
}: PlaygroundObservabilityScreenProps) {
  return (
    <PageShell
      title="Observability components"
      description="Demo of WorkflowTimeline (spec 06 §8) and GitOpsCommitTimeline (spec 07 §12). Real data wires in via the deployment-detail and workflow-detail pages."
    >
      <section className="space-y-3">
        <h2 className="text-lg font-medium">Workflow run</h2>
        <WorkflowTimeline
          run={run}
          activities={activities}
          onCancel={onCancel}
          onRetryActivity={onRetryActivity}
        />
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">GitOps commit timeline</h2>
        <GitOpsCommitTimeline commits={commits} />
      </section>
    </PageShell>
  );
}
