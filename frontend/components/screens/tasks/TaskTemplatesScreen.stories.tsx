import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { LONG_TASK_WORKLOADS, TASK_WORKLOADS } from "@/components/screens/jobs/jobs-tasks.fixtures";

import { TaskTemplatesScreen, type TaskTemplatesScreenProps } from "./TaskTemplatesScreen";
import { TASK_TEMPLATES_LIST } from "./use-tasks";

/** Task templates: the container tasks an app registers, with Run now. */
const meta: Meta = {
  title: "Screens/Tasks/TaskTemplatesScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const noop = () => {};

type Props = Partial<Omit<TaskTemplatesScreenProps, "list">> & { initial?: Partial<ListState> };

function Templates({ initial, ...props }: Props) {
  const list = useLocalListState(TASK_TEMPLATES_LIST, initial);
  return (
    <TaskTemplatesScreen
      list={list}
      rows={TASK_WORKLOADS}
      loading={false}
      stale={false}
      error={null}
      onRetry={noop}
      nextCursor="c2"
      runningWorkloadId={null}
      mutationLoading={false}
      onRunNow={noop}
      {...props}
    />
  );
}

export const Full: Story = { render: () => <Templates /> };

/** A template being started: its button spins, the rest are disabled. */
export const Starting: Story = {
  render: () => <Templates runningWorkloadId={TASK_WORKLOADS[0].id} mutationLoading />,
};

export const Loading: Story = {
  render: () => <Templates rows={[]} loading nextCursor={null} />,
};

export const Empty: Story = {
  render: () => <Templates rows={[]} nextCursor={null} />,
};

/** Mine has nothing to answer with yet: the note says why. */
export const EmptyMine: Story = {
  render: () => <Templates initial={{ view: "mine" }} rows={[]} nextCursor={null} />,
};

export const LoadError: Story = {
  render: () => (
    <Templates
      rows={[]}
      nextCursor={null}
      error={{ message: "Response not successful: Received status code 502" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Templates rows={LONG_TASK_WORKLOADS} nextCursor={null} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Templates rows={[...LONG_TASK_WORKLOADS, ...TASK_WORKLOADS]} />
    </div>
  ),
};
