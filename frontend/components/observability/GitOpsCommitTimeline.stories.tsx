import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { GitOpsCommitTimeline } from "@/components/observability/GitOpsCommitTimeline";
import type { GitOpsCommit } from "@/components/observability/types";

const meta: Meta = { title: "Patterns/Observability/GitOpsCommitTimeline" };
export default meta;

const COMMITS: GitOpsCommit[] = [
  {
    hash: "1112015d8a9b7c6e5f4d3c2b1a0f9e8d7c6b5a49",
    author: "astrolift-bot",
    message: "checkout: image 1112015d → production",
    occurredAt: "2026-09-28T12:04:00Z",
    diffUrl: "#",
  },
  {
    hash: "9a0c11ee5b3f4d2c1b0a9f8e7d6c5b4a39281706",
    author: "leo",
    message: "checkout: raise replicas to 3",
    occurredAt: "2026-09-28T09:40:00Z",
    sourceCommitUrl: "#",
  },
];

export const Default: StoryObj = { render: () => <GitOpsCommitTimeline commits={COMMITS} /> };
export const Empty: StoryObj = {
  render: () => <GitOpsCommitTimeline commits={[]} emptyMessage="No config commits yet." />,
};

/** Older commits behind the caller's cursor: Load older at the end of the frame. */
export const HasOlder: StoryObj = {
  render: () => <GitOpsCommitTimeline commits={COMMITS} hasMore onLoadMore={() => {}} />,
};

export const Loading: StoryObj = { render: () => <GitOpsCommitTimeline commits={[]} loading /> };

export const ErrorState: StoryObj = {
  render: () => (
    <GitOpsCommitTimeline commits={[]} error="config repo unreachable" onRetry={() => {}} />
  ),
};

const LONG_SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

export const LongStrings: StoryObj = {
  render: () => (
    <GitOpsCommitTimeline
      commits={[
        {
          hash: LONG_SHA,
          author: `astrolift-bot-${"x".repeat(80)}@example.com`,
          message: `checkout: image ${LONG_SHA} → https://registry.example.com/${"a".repeat(160)}`,
          occurredAt: "2026-09-28T12:04:00Z",
          workflowRunId: LONG_SHA,
          diffUrl: "#",
        },
        ...COMMITS,
      ]}
    />
  ),
};

export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <GitOpsCommitTimeline commits={COMMITS} hasMore />
    </div>
  ),
};
