import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";

import {
  JOB_RUNS,
  type JobRunsFixture,
  LONG_FAILED_RUN,
  LONG_JOB_RUNS,
  runListProps,
} from "./jobs-tasks.fixtures";
import { JOB_RUNS_LIST, jobRunsVariables } from "./jobs-list";
import { JobRunsScreen } from "./JobRunsScreen";

const meta: Meta = {
  title: "Screens/Jobs/JobRunsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Runs({
  runs = JOB_RUNS,
  initial,
  ...patch
}: Partial<JobRunsFixture> & { runs?: AstroliftScheduledJobRun[]; initial?: Partial<ListState> }) {
  const list = useLocalListState(JOB_RUNS_LIST, initial);
  // Stands in for `astroliftScheduledJobRunsPage`, applying what the hook sends.
  const v = jobRunsVariables(list.filters, list.state);
  const served = runs.filter(
    (r) =>
      (!v.appSlug || r.registeredAppSlug === v.appSlug) &&
      (!v.workloadSlug || r.workloadSlug === v.workloadSlug) &&
      (!v.filter?.status || v.filter.status.includes(r.status)) &&
      (!v.filter?.triggeredBy || r.triggeredByMe)
  );
  return <JobRunsScreen {...runListProps(served)} {...patch} list={list} />;
}

export const Full: Story = { render: () => <Runs /> };

export const Loading: Story = { render: () => <Runs runs={[]} loading /> };

export const Empty: Story = { render: () => <Runs runs={[]} totalCount={0} /> };

export const ErrorState: Story = {
  render: () => <Runs runs={[]} error={{ message: "upstream timed out" }} />,
};

export const Failed: Story = { render: () => <Runs initial={{ view: "failed" }} /> };

/** Mine: the runs the viewer started with Run now. */
export const Mine: Story = {
  render: () => (
    <Runs
      runs={JOB_RUNS.map((r, i) =>
        i === 1 ? { ...r, triggerKind: "manual", triggeredByMe: true } : r
      )}
      initial={{ view: "mine" }}
    />
  ),
};

/** One job's runs, as a job row opens them. */
export const OneJob: Story = {
  render: () => <Runs initial={{ filters: { app: "billing", workload: "nightly-report" } }} />,
};

export const NewRows: Story = {
  render: () => <Runs newRows={{ count: 3, onReveal: () => {} }} />,
};

export const LongStrings: Story = {
  render: () => <Runs runs={[...LONG_JOB_RUNS, LONG_FAILED_RUN, ...JOB_RUNS]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Runs />
    </div>
  ),
};
