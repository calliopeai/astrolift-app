import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, TASK, TASK_RUNS } from "./app-homes.fixtures";
import { TaskHomeScreen } from "./TaskHome";

const meta: Meta = {
  title: "Screens/Apps/Homes/TaskHome",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <TaskHomeScreen {...TASK} /> };

export const Loading: Story = { render: () => <TaskHomeScreen {...TASK} runs={[]} loading /> };

export const Empty: Story = { render: () => <TaskHomeScreen {...TASK} runs={[]} /> };

export const LatestRunFailed: Story = {
  render: () => <TaskHomeScreen {...TASK} runs={TASK_RUNS.slice(2)} />,
};

export const Succeeded: Story = {
  render: () => <TaskHomeScreen {...TASK} runs={TASK_RUNS.slice(1)} />,
};

export const LongStrings: Story = { render: () => <TaskHomeScreen {...TASK} name={LONG} /> };

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <TaskHomeScreen {...TASK} runs={TASK_RUNS.slice(2)} />
    </div>
  ),
};

export const QueryFailed: Story = {
  render: () => (
    <TaskHomeScreen
      {...TASK}
      runs={[]}
      error={{ name: "Error", message: "Permission denied while loading task runs" }}
    />
  ),
};
