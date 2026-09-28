import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_TASK_RUNS, TASK_RUNS } from "@/components/screens/jobs/jobs-tasks.fixtures";

import { TaskRunDetail } from "./TaskRunDetail";

const meta: Meta = {
  title: "Screens/Tasks/TaskRunDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const DETAIL = { id: TASK_RUNS[1].id, loading: false, run: TASK_RUNS[1] };

export const Full: Story = {
  render: () => <TaskRunDetail {...DETAIL} />,
};

/** Still running: no exit code, no end time. */
export const Running: Story = {
  render: () => <TaskRunDetail id={TASK_RUNS[0].id} loading={false} run={TASK_RUNS[0]} />,
};

/** Failed, triggered through the API with no operator. */
export const Failed: Story = {
  render: () => <TaskRunDetail id={TASK_RUNS[2].id} loading={false} run={TASK_RUNS[2]} />,
};

export const Loading: Story = {
  render: () => <TaskRunDetail {...DETAIL} loading run={null} />,
};

/**
 * No run with this id. The detail has no separate error state: a failed
 * fetch with nothing cached resolves to this same not-found view.
 */
export const NotFound: Story = {
  render: () => <TaskRunDetail {...DETAIL} run={null} />,
};

export const LongStrings: Story = {
  render: () => <TaskRunDetail id={LONG_TASK_RUNS[0].id} loading={false} run={LONG_TASK_RUNS[0]} />,
};
