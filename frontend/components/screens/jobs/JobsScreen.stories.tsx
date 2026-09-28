import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";

import {
  COMMAND_RUNS,
  CRON_TABLE,
  JOBS,
  LONG_COMMAND_RUNS,
  LONG_JOB_RUNS,
} from "./jobs-tasks.fixtures";
import { CronWorkloadsTable, JobsScreen } from "./JobsScreen";

const meta: Meta = {
  title: "Screens/Jobs/JobsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const cron = <CronWorkloadsTable {...CRON_TABLE} />;

/** Per-app Schedules tab: cron workloads with an env pick and Run now. */
export const Full: Story = {
  render: () => <JobsScreen {...JOBS} cronWorkloads={cron} />,
};

export const Runs: Story = {
  render: () => <JobsScreen {...JOBS} tab="runs" />,
};

export const Failures: Story = {
  render: () => <JobsScreen {...JOBS} tab="failures" />,
};

export const Commands: Story = {
  render: () => <JobsScreen {...JOBS} tab="commands" />,
};

export const Logs: Story = {
  render: () => <JobsScreen {...JOBS} tab="logs" />,
};

/** Fleet-wide /jobs: no app to anchor schedules to, so the tab points at the per-app view. */
export const FleetSchedules: Story = {
  render: () => <JobsScreen {...JOBS} appSlug={undefined} />,
};

/** A job dispatching: its Run now shows a spinner; one environment hides the select. */
export const RunPending: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      cronWorkloads={
        <CronWorkloadsTable
          {...CRON_TABLE}
          envList={CRON_TABLE.envList.slice(0, 1)}
          pendingSlug="nightly-report"
        />
      }
    />
  ),
};

/** First paint: badge counts unknown, the runs table skeleton. */
export const Loading: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      tab="runs"
      summaryRuns={[]}
      scheduleCount={null}
      runCount={null}
      failureCount={null}
      commandCount={null}
      runsTable={fakeController({ state: "loading" })}
    />
  ),
};

/** Empty schedules: the empty table plus the worked astrolift.toml example under it. */
export const Empty: Story = {
  render: () => {
    const schedulesController = fakeController<never>({ state: "empty", searchEnabled: false });
    return (
      <JobsScreen
        {...JOBS}
        scheduleCount={0}
        schedulesController={schedulesController}
        cronWorkloads={<CronWorkloadsTable {...CRON_TABLE} controller={schedulesController} />}
      />
    );
  },
};

export const EmptyRuns: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      tab="runs"
      summaryRuns={[]}
      runCount={0}
      runsTable={fakeController({ state: "empty" })}
    />
  ),
};

export const EmptyFailures: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      tab="failures"
      failureCount={0}
      failuresController={fakeController({ state: "empty", searchEnabled: false })}
    />
  ),
};

export const NoMatchingCommands: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      tab="commands"
      commandsTable={fakeController({ state: "emptyFiltered", search: "rake", isFiltered: true })}
    />
  ),
};

export const LoadError: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      tab="commands"
      commandsTable={fakeController({
        state: "error",
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      appSlug="customer-billing-reconciliation-service-with-an-unusually-long-slug"
      tab="runs"
      runsTable={fakeController({ rows: LONG_JOB_RUNS })}
    />
  ),
};

export const LongCommands: Story = {
  render: () => (
    <JobsScreen
      {...JOBS}
      tab="commands"
      commandsTable={fakeController({ rows: [...LONG_COMMAND_RUNS, ...COMMAND_RUNS] })}
    />
  ),
};
