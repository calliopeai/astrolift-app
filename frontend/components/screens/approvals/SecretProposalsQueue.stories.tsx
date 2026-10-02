import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_PROPOSAL, QUEUE, proposalMetadata } from "./approvals-b.fixtures";
import { SecretProposalsQueue } from "./SecretProposalsQueue";

const meta: Meta<typeof SecretProposalsQueue> = {
  title: "Screens/Approvals/SecretProposalsQueue",
  component: SecretProposalsQueue,
  args: QUEUE,
};
export default meta;

type Story = StoryObj<typeof SecretProposalsQueue>;

export const Full: Story = {};
export const Loading: Story = { args: { rows: [], loading: true } };
export const Empty: Story = { args: { rows: [], totalCount: 0 } };
export const QueryFailed: Story = {
  args: {
    rows: [],
    error: { name: "Error", message: "Permission denied while loading this section" },
  },
};
export const LongStrings: Story = { args: { rows: [proposalMetadata(LONG_PROPOSAL)] } };
export const Continuation: Story = { args: { nextCursor: "opaque-next-cursor", totalCount: 237 } };
export const StaleCursor: Story = {
  args: {
    error: {
      name: "GraphQLError",
      message: "Secret proposal queue changed. Restart from the first page.",
    },
    nextCursor: null,
    totalCount: null,
  },
};
