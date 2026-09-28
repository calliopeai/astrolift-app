import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { historyProps, LONG_HISTORY_TASKS } from "./agents-list.fixtures";
import { TaskHistoryPanel } from "./TaskHistoryPanel";

const meta: Meta = { title: "Screens/Agents/List/TaskHistoryPanel" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <TaskHistoryPanel {...historyProps()} /> };

export const Loading: Story = {
  render: () => <TaskHistoryPanel {...historyProps({ tasks: [], loading: true })} />,
};

export const Empty: Story = { render: () => <TaskHistoryPanel {...historyProps({ tasks: [] })} /> };

/** The History tab has no error state: a failed query renders the empty card. */
export const QueryFailedShowsEmpty: Story = {
  render: () => <TaskHistoryPanel {...historyProps({ tasks: [], loading: false })} />,
};

export const LongStrings: Story = {
  render: () => <TaskHistoryPanel {...historyProps({ tasks: LONG_HISTORY_TASKS })} />,
};
