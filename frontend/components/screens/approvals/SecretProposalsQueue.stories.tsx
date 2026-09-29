import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_PROPOSAL, QUEUE } from "./approvals-b.fixtures";
import { SecretProposalsQueue } from "./SecretProposalsQueue";

const meta: Meta<typeof SecretProposalsQueue> = {
  title: "Screens/Approvals/SecretProposalsQueue",
  component: SecretProposalsQueue,
  args: QUEUE,
};
export default meta;

type Story = StoryObj<typeof SecretProposalsQueue>;

export const Full: Story = {};

export const Loading: Story = { args: { proposals: [], loading: true } };

export const Empty: Story = { args: { proposals: [] } };

/** The queue has no error state; a failed query renders as empty. */
export const QueryFailed: Story = { args: { proposals: [] } };

export const LongStrings: Story = { args: { proposals: [LONG_PROPOSAL] } };
