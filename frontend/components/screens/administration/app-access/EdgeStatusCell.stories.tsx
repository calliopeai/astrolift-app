import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EDGE_STATUSES } from "./edge-access-list";
import { EdgeStatusCell } from "./EdgeStatusCell";

/** Whether the edge holds a rule (#2534): every status, with what it means or what fixes it. */
const meta: Meta<typeof EdgeStatusCell> = {
  title: "Screens/Administration/AppAccess/EdgeStatusCell",
  component: EdgeStatusCell,
};
export default meta;

type Story = StoryObj<typeof EdgeStatusCell>;

export const Enforced: Story = { args: { status: "enforced", appliedAt: "2026-10-26T14:02:11Z" } };

export const Applying: Story = { args: { status: "applying", appliedAt: "2026-10-26T14:02:11Z" } };

export const PendingDeploy: Story = { args: { status: "pending_deploy", appliedAt: null } };

/** Every status in one column, as the table shows them. */
export const All: Story = {
  render: () => (
    <div className="flex w-60 flex-col gap-3">
      {EDGE_STATUSES.map((status) => (
        <EdgeStatusCell key={status} status={status} appliedAt="2026-10-26T14:02:11Z" />
      ))}
    </div>
  ),
};
