import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_TASK_RUNS, TASK_RUNS } from "@/components/screens/jobs/jobs-tasks.fixtures";

import { TaskRunDetail, type TaskRunDetailProps } from "./TaskRunDetail";

/** One container task run on the run archetype (spec 44 §5.5). */
const meta: Meta = {
  title: "Screens/Tasks/TaskRunDetail",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const NOW = Date.parse("2026-09-28T09:31:00Z");

const DETAIL: TaskRunDetailProps = {
  id: TASK_RUNS[1].id,
  loading: false,
  run: TASK_RUNS[1],
  error: null,
  onRetry: () => {},
  now: NOW,
};

export const Full: Story = {
  render: () => <TaskRunDetail {...DETAIL} />,
};

/** Still running: no exit code, no end time, the clock ticking. */
export const Running: Story = {
  render: () => <TaskRunDetail {...DETAIL} id={TASK_RUNS[0].id} run={TASK_RUNS[0]} />,
};

/** Failed, triggered through the API with no operator: the exit code first. */
export const Failed: Story = {
  render: () => <TaskRunDetail {...DETAIL} id={TASK_RUNS[2].id} run={TASK_RUNS[2]} />,
};

/** Waiting for a pod. */
export const Pending: Story = {
  render: () => <TaskRunDetail {...DETAIL} id={TASK_RUNS[3].id} run={TASK_RUNS[3]} />,
};

export const Loading: Story = {
  render: () => <TaskRunDetail {...DETAIL} loading run={null} />,
};

/** No run with this id, or no access to it. */
export const NotFound: Story = {
  render: () => <TaskRunDetail {...DETAIL} run={null} />,
};

export const LoadError: Story = {
  render: () => (
    <TaskRunDetail
      {...DETAIL}
      run={null}
      error={{ message: "Response not successful: Received status code 502" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <TaskRunDetail {...DETAIL} id={LONG_TASK_RUNS[0].id} run={LONG_TASK_RUNS[0]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <TaskRunDetail {...DETAIL} id={LONG_TASK_RUNS[0].id} run={LONG_TASK_RUNS[0]} />
    </div>
  ),
};
