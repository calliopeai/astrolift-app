import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ActiveTasksPanel } from "./ActiveTasksPanel";
import { activeProps, LONG_TASKS } from "./agents-list.fixtures";

const meta: Meta = { title: "Screens/Agents/List/ActiveTasksPanel" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <ActiveTasksPanel {...activeProps()} /> };

export const Loading: Story = {
  render: () => <ActiveTasksPanel {...activeProps({ tasks: [], loading: true })} />,
};

export const Empty: Story = { render: () => <ActiveTasksPanel {...activeProps({ tasks: [] })} /> };

/** The Active tab has no error state: a failed query renders the empty card. */
export const QueryFailedShowsEmpty: Story = {
  render: () => <ActiveTasksPanel {...activeProps({ tasks: [], loading: false })} />,
};

/** Polling refetch with rows already on screen keeps the table, not the skeleton. */
export const Refetching: Story = {
  render: () => <ActiveTasksPanel {...activeProps({ loading: true })} />,
};

export const LongStrings: Story = {
  render: () => <ActiveTasksPanel {...activeProps({ tasks: LONG_TASKS })} />,
};
