import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_GATE, PENDING_GATES } from "./approvals-b.fixtures";
import { PendingGatesScreen } from "./PendingGates";

const meta: Meta<typeof PendingGatesScreen> = {
  title: "Screens/Approvals/PendingGates",
  component: PendingGatesScreen,
  parameters: { layout: "fullscreen" },
  args: PENDING_GATES,
};
export default meta;

type Story = StoryObj<typeof PendingGatesScreen>;

export const Full: Story = {};

export const Loading: Story = { args: { gates: [], loading: true } };

export const Empty: Story = { args: { gates: [] } };

export const QueryError: Story = {
  args: { gates: [], error: "Failed to fetch pending gates: network unreachable" },
};

export const LongStrings: Story = { args: { gates: [LONG_GATE] } };
