import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SECRET_SUMMARY } from "./approvals-b.fixtures";
import { SecretProposalsSummary } from "./SecretProposalsSummary";
const meta: Meta<typeof SecretProposalsSummary> = {
  title: "Screens/Approvals/SecretProposalsSummary",
  component: SecretProposalsSummary,
  args: SECRET_SUMMARY,
};
export default meta;
type Story = StoryObj<typeof SecretProposalsSummary>;
export const Full: Story = {};
export const Loading: Story = { args: { rows: [], loading: true } };
export const Empty: Story = { args: { rows: [], count: 0 } };
export const LargeQueue: Story = { args: { count: 237 } };
export const Refused: Story = {
  args: { error: { name: "Error", message: "Permission denied" }, count: null },
};
