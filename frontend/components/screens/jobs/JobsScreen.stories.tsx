import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { CRON_WORKLOADS, jobsProps, LONG_CRON_WORKLOADS, RECENT } from "./jobs-tasks.fixtures";
import { type CronWorkload, JOBS_LIST, selectJobs, withLastRuns } from "./jobs-list";
import { JobsScreen, type JobsScreenProps } from "./JobsScreen";

const meta: Meta = {
  title: "Screens/Jobs/JobsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<JobsScreenProps, "list">> & {
  jobs?: CronWorkload[];
  initial?: Partial<ListState>;
};

/** The screen over fixture jobs, filtered and paged the way the hook does it. */
function Jobs({ jobs = CRON_WORKLOADS, initial, ...patch }: Props) {
  const list = useLocalListState(JOBS_LIST, initial);
  const { rows, totalCount } = selectJobs(withLastRuns(jobs, RECENT), {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return <JobsScreen {...jobsProps({ rows, totalCount, ...patch })} list={list} />;
}

/** All: every cron job with its schedule, concurrency and last run. */
export const Full: Story = { render: () => <Jobs /> };

export const Loading: Story = { render: () => <Jobs jobs={[]} loading /> };

/** No cron job declared anywhere: the manifest reference. */
export const Empty: Story = { render: () => <Jobs jobs={[]} /> };

export const EmptyFiltered: Story = {
  render: () => <Jobs initial={{ filters: { concurrency: "queue" } }} />,
};

export const ErrorState: Story = {
  render: () => <Jobs jobs={[]} error={{ message: "upstream timed out" }} />,
};

/** Failing: jobs whose latest run failed. */
export const Failing: Story = { render: () => <Jobs initial={{ view: "failing" }} /> };

/** Paused: no data behind it yet, so empty, with the note saying why. */
export const Paused: Story = { render: () => <Jobs initial={{ view: "paused" }} /> };

/** Mine: empty, with the note saying why. */
export const Mine: Story = { render: () => <Jobs initial={{ view: "mine" }} /> };

/** Embedded on an app's Workloads tab: no header, the views in the filter bar. */
export const Embedded: Story = {
  render: () => <Jobs embedded appSlug="billing" />,
};

/** A run in flight from Run now. */
export const Running: Story = { render: () => <Jobs pendingJob="billing/nightly-report" /> };

export const LongStrings: Story = {
  render: () => <Jobs jobs={[...LONG_CRON_WORKLOADS, ...CRON_WORKLOADS]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Jobs />
    </div>
  ),
};
