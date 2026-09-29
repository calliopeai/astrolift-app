import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type { WorkflowActivity, WorkflowRunSummary } from "@/components/observability/types";
import { WorkflowTimeline } from "@/components/observability/WorkflowTimeline";

const meta: Meta = { title: "Patterns/Observability/WorkflowTimeline" };
export default meta;

const RUN: WorkflowRunSummary = {
  workflowId: "nightly-sync",
  runId: "7d02e5f7-4b1a-4c9e-9f3a-1d2c3b4a5f60",
  workflowKind: "WorkflowRun",
  status: "running",
  startedAt: "2026-09-28T12:00:00Z",
};

const ACTIVITIES: WorkflowActivity[] = [
  {
    id: "a1",
    name: "fetch",
    status: "succeeded",
    durationMs: 12000,
    attempts: [
      {
        attemptNumber: 1,
        startedAt: "2026-09-28T12:00:00Z",
        endedAt: "2026-09-28T12:00:12Z",
        status: "succeeded",
      },
    ],
  },
  {
    id: "a2",
    name: "transform",
    status: "failed",
    durationMs: 51000,
    attempts: [
      {
        attemptNumber: 1,
        startedAt: "2026-09-28T12:00:12Z",
        endedAt: "2026-09-28T12:00:40Z",
        status: "failed",
        errorMessage: "tool timeout after 45s",
      },
      {
        attemptNumber: 2,
        startedAt: "2026-09-28T12:00:41Z",
        endedAt: "2026-09-28T12:01:03Z",
        status: "failed",
        errorMessage: "tool timeout after 45s",
      },
    ],
  },
  { id: "a3", name: "publish", status: "pending", attempts: [] },
];

export const Running: StoryObj = {
  render: () => (
    <WorkflowTimeline
      run={RUN}
      activities={ACTIVITIES}
      onCancel={() => {}}
      onRetryActivity={() => {}}
    />
  ),
};
export const Failed: StoryObj = {
  render: () => (
    <WorkflowTimeline
      run={{
        ...RUN,
        status: "failed",
        endedAt: "2026-09-28T12:01:03Z",
        errorMessage: "activity transform failed after 2 attempts",
      }}
      activities={ACTIVITIES}
    />
  ),
};

export const Empty: StoryObj = {
  render: () => <WorkflowTimeline run={RUN} activities={[]} />,
};

/** A long history on the caller's cursor: Load older at the end of the frame. */
export const HasOlder: StoryObj = {
  render: () => (
    <WorkflowTimeline run={RUN} activities={ACTIVITIES} hasMore onLoadMore={() => {}} />
  ),
};

export const LongStrings: StoryObj = {
  render: () => (
    <WorkflowTimeline
      run={{
        ...RUN,
        workflowId: `nightly-sync-${"x".repeat(180)}`,
        status: "failed",
        errorMessage: `https://temporal.example.com/${"a".repeat(200)}`,
      }}
      activities={[
        {
          ...ACTIVITIES[0]!,
          name: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        },
        ...ACTIVITIES.slice(1),
      ]}
    />
  ),
};

export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <WorkflowTimeline run={RUN} activities={ACTIVITIES} />
    </div>
  ),
};
