import type { WorkflowFrameSubject } from "./workflow-frame-subject";
import type { WorkflowFrameProps } from "./WorkflowFrame";

/** Hand-typed fixtures for the workflow frame: the header data and its callbacks. */

export const LONG =
  "platform-team-shared-production-nightly-reconciliation-workflow-with-a-deliberately-long-name-that-keeps-going";

export const SUBJECT: WorkflowFrameSubject = {
  kind: "configured",
  id: "3f1c9a52-7d44-4b0e-9a1e-2c8f6b1d0a11",
  slug: "nightly-sync",
  name: "Nightly sync",
  isEnabled: true,
  patternKind: "sequential",
  stageCount: 4,
  projectSlug: "revenue-ops",
  lastRun: {
    guid: "8b2d4e61-1a3f-4c7d-8e2b-5f6a7c8d9e02",
    state: "completed",
    startedAt: "2026-09-27T04:00:00Z",
    live: false,
    temporalWorkflowId: "wf-nightly-sync-20260927-0400",
    temporalRunId: "run-1d2e3f",
  },
};

export const FRAME: Omit<WorkflowFrameProps, "children"> = {
  slug: SUBJECT.slug,
  pathname: `/workflows/${SUBJECT.slug}`,
  workflow: SUBJECT,
  loading: false,
  error: null,
  onRetry: () => {},
  failedRun: null,
  canRun: true,
  canDelete: true,
  dispatching: false,
  onRun: async () => true,
  onCopyId: () => {},
};

export const FAILING_FRAME: Omit<WorkflowFrameProps, "children"> = {
  ...FRAME,
  workflow: { ...SUBJECT, lastRun: { ...SUBJECT.lastRun!, state: "failed" } },
  failedRun: {
    href: `/workflows/${SUBJECT.slug}/runs`,
    reason:
      "Stage 2 (warehouse-load) failed: AccessDenied assuming arn:aws:iam::123456789012:role/astrolift-workflow-runtime-conflict-astrolift-production-us-west-2-with-a-deliberately-long-suffix-for-overflow at https://sts.us-west-2.amazonaws.com/with/an/unbroken/path/that/keeps/going",
  },
};

export const RUNNING_SUBJECT: WorkflowFrameSubject = {
  ...SUBJECT,
  lastRun: { ...SUBJECT.lastRun!, state: "running", live: true },
};

export const DISABLED_SUBJECT: WorkflowFrameSubject = {
  ...SUBJECT,
  isEnabled: false,
  lastRun: null,
};

export const DEFINITION_SUBJECT: WorkflowFrameSubject = {
  kind: "definition",
  id: "def-outbound",
  slug: "outbound-pipeline",
  name: "Outbound pipeline",
  isEnabled: true,
  patternKind: "fan_out_aggregate",
  stageCount: 1,
  projectSlug: null,
  lastRun: null,
};

export const LONG_FRAME: Omit<WorkflowFrameProps, "children"> = {
  ...FRAME,
  slug: LONG,
  pathname: `/workflows/${LONG}/triggers`,
  workflow: {
    ...SUBJECT,
    slug: LONG,
    name: LONG,
    patternKind: "fan_out_aggregate_with_human_review_and_retry_on_every_stage",
    stageCount: 128,
    projectSlug: `project-${LONG}`,
  },
};
