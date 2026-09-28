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
