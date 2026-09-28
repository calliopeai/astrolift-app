import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import {
  LONG_TASK_RUNS,
  LONG_TASK_WORKLOADS,
  TASK_RUNS,
  TASK_WORKLOADS,
} from "@/components/screens/jobs/jobs-tasks.fixtures";

import { TaskRunsTable, TaskTemplatesTable, TasksScreen } from "./TasksScreen";

const meta: Meta = {
  title: "Screens/Tasks/TasksScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const noop = () => {};

const templates = (
  <TaskTemplatesTable
    controller={fakeController({ rows: TASK_WORKLOADS, searchEnabled: false })}
    runningWorkloadId={null}
    onRunNow={noop}
    mutationLoading={false}
  />
);
const recent = <TaskRunsTable kind="recent" controller={fakeController({ rows: TASK_RUNS })} />;
const history = (
  <TaskRunsTable
    kind="history"
    status=""
    onStatusChange={noop}
    controller={fakeController({ rows: TASK_RUNS, totalCount: 42, hasNext: true })}
  />
);
const slots = { templates, recent, history };

export const Full: Story = {
  render: () => <TasksScreen tab="templates" setTab={noop} {...slots} />,
};

/** A template being started: its button spins, the rest are disabled. */
export const Starting: Story = {
  render: () => (
    <TasksScreen
      tab="templates"
      setTab={noop}
      {...slots}
      templates={
        <TaskTemplatesTable
          controller={fakeController({ rows: TASK_WORKLOADS, searchEnabled: false })}
          runningWorkloadId={TASK_WORKLOADS[0].id}
          onRunNow={noop}
          mutationLoading
        />
      }
    />
  ),
};

export const Recent: Story = {
  render: () => <TasksScreen tab="recent" setTab={noop} {...slots} />,
};

export const History: Story = {
  render: () => <TasksScreen tab="history" setTab={noop} {...slots} />,
};

export const Logs: Story = {
  render: () => <TasksScreen tab="logs" setTab={noop} {...slots} />,
};

export const Loading: Story = {
  render: () => (
    <TasksScreen
      tab="templates"
      setTab={noop}
      {...slots}
      templates={
        <TaskTemplatesTable
          controller={fakeController({ state: "loading" })}
          runningWorkloadId={null}
          onRunNow={noop}
          mutationLoading={false}
        />
      }
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <TasksScreen
      tab="templates"
      setTab={noop}
      {...slots}
      templates={
        <TaskTemplatesTable
          controller={fakeController({ state: "empty", searchEnabled: false })}
          runningWorkloadId={null}
          onRunNow={noop}
          mutationLoading={false}
        />
      }
    />
  ),
};

export const EmptyRecent: Story = {
  render: () => (
    <TasksScreen
      tab="recent"
      setTab={noop}
      {...slots}
      recent={<TaskRunsTable kind="recent" controller={fakeController({ state: "empty" })} />}
    />
  ),
};

/** History filtered to a status with no runs: the empty copy names the filter. */
export const EmptyHistoryForStatus: Story = {
  render: () => (
    <TasksScreen
      tab="history"
      setTab={noop}
      {...slots}
      history={
        <TaskRunsTable
          kind="history"
          status="cancelled"
          onStatusChange={noop}
          controller={fakeController({ state: "empty" })}
        />
      }
    />
  ),
};

export const LoadError: Story = {
  render: () => (
    <TasksScreen
      tab="recent"
      setTab={noop}
      {...slots}
      recent={
        <TaskRunsTable
          kind="recent"
          controller={fakeController({
            state: "error",
            error: new globalThis.Error("upstream timed out"),
          })}
        />
      }
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <TasksScreen
      tab="recent"
      setTab={noop}
      {...slots}
      recent={
        <TaskRunsTable
          kind="recent"
          controller={fakeController({ rows: [...LONG_TASK_RUNS, ...TASK_RUNS] })}
        />
      }
    />
  ),
};

export const LongTemplates: Story = {
  render: () => (
    <TasksScreen
      tab="templates"
      setTab={noop}
      {...slots}
      templates={
        <TaskTemplatesTable
          controller={fakeController({ rows: [...LONG_TASK_WORKLOADS, ...TASK_WORKLOADS] })}
          runningWorkloadId={null}
          onRunNow={noop}
          mutationLoading={false}
        />
      }
    />
  ),
};
