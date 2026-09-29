import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { RunStateBadge } from "./RunStateBadge";

const meta: Meta<typeof RunStateBadge> = {
  title: "Screens/Workflows/Detail/RunStateBadge",
  component: RunStateBadge,
};
export default meta;

type Story = StoryObj<typeof RunStateBadge>;

export const Running: Story = { args: { state: "running" } };
export const Completed: Story = { args: { state: "completed" } };
export const Failed: Story = { args: { state: "failed" } };
export const Cancelled: Story = { args: { state: "cancelled" } };

/** Every state the engine reports, side by side. */
export const AllStates: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      {["pending", "running", "completed", "failed", "cancelled", "terminated", "timed_out"].map(
        (state) => (
          <RunStateBadge key={state} state={state} />
        )
      )}
    </div>
  ),
};

export const LongStrings: Story = {
  args: { state: "continued_as_new_after_history_size_limit_reached_waiting_for_worker" },
};
