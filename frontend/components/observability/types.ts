/**
 * Observability types — workflow runs and GitOps commits.
 *
 * Two surfaces ship here:
 *  - WorkflowTimeline (spec 06 §8) — render a Temporal workflow run
 *    with its activities, retries, and operator actions.
 *  - GitOpsCommitTimeline (spec 07 §12) — config-repo commits inline
 *    in a deployment timeline, linked to their workflow run.
 *
 * Components take the data as props; wiring to LIST_WORKFLOW_RUNS /
 * config-repo audit happens in the page that mounts them.
 */

export type WorkflowStatus =
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "terminated";

export type ActivityStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface ActivityAttempt {
  attemptNumber: number;
  startedAt: string;
  endedAt?: string | null;
  status: ActivityStatus;
  errorMessage?: string;
  errorStack?: string;
}

export interface WorkflowActivity {
  id: string;
  name: string;
  status: ActivityStatus;
  attempts: ActivityAttempt[];
  /** Total elapsed time across all attempts, in milliseconds. */
  durationMs?: number;
}

export interface WorkflowRunSummary {
  workflowId: string;
  runId: string;
  workflowKind: string;
  status: WorkflowStatus;
  startedAt: string;
  endedAt?: string | null;
  errorMessage?: string;
  errorStack?: string;
  /** Optional deep link into the Temporal UI for this run. */
  temporalUiUrl?: string;
}

export interface GitOpsCommit {
  hash: string;
  author: string;
  authorEmail?: string;
  message: string;
  occurredAt: string;
  /** Link to the diff against the previous commit (config repo UI). */
  diffUrl?: string;
  /** Link to the source-repo commit that triggered this config commit. */
  sourceCommitUrl?: string;
  /** Workflow run ID that produced this commit, if known. */
  workflowRunId?: string;
}
