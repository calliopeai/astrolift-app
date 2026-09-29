import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DETAIL, LONG_PROPOSAL, PROPOSAL } from "./approvals-b.fixtures";
import { SecretProposalDetailScreen } from "./SecretProposalDetail";

const meta: Meta<typeof SecretProposalDetailScreen> = {
  title: "Screens/Approvals/SecretProposalDetail",
  component: SecretProposalDetailScreen,
  parameters: { layout: "fullscreen" },
  args: DETAIL,
};
export default meta;

type Story = StoryObj<typeof SecretProposalDetailScreen>;

export const Full: Story = {};

export const Loading: Story = { args: { proposal: null, loading: true } };

/** No proposal with this id; also what a failed query shows (there is no error state). */
export const NotFound: Story = { args: { proposal: null } };

/** Pending, but the viewer lacks secret.approve: only Withdraw. */
export const WithoutApprovePermission: Story = { args: { canApprove: false } };

export const Approving: Story = { args: { approving: true } };

/** Decided proposal: no actions, no expiry; empty diff and approver list. */
export const Decided: Story = {
  args: {
    proposal: {
      ...PROPOSAL,
      status: "rejected",
      payloadDiff: {},
      approvals: [],
    },
  },
};

export const LongStrings: Story = { args: { proposal: LONG_PROPOSAL } };
