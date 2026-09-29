import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { CRON_WORKLOADS, JOB_RUNS, jobsProps, LONG_CRON_WORKLOADS } from "./jobs-tasks.fixtures";
import { type CronWorkload, JOBS_LIST, type JobRow, jobsVariables, narrowJobs } from "./jobs-list";
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

/** Each job with its latest run from the fixture runs, as the page returns it. */
function withLatestRun(jobs: CronWorkload[]): JobRow[] {
  return jobs.map((j) => {
    const run = JOB_RUNS.find(
      (r) => r.registeredAppSlug === j.registeredAppSlug && r.workloadSlug === j.slug
    );
    return {
      ...j,
      lastRun: run
        ? {
            id: run.id,
            status: run.status,
            startedAt: run.startedAt ?? null,
            createdAt: run.createdAt,
          }
        : null,
    };
  });
}

/**
 * The screen over fixture jobs. The story stands in for the server (app and
 * Mine from the variables the hook sends; nobody owns a fixture job), then
 * narrows the page the way the hook does for Failing and Paused.
 */
function Jobs({ jobs = CRON_WORKLOADS, initial, ...patch }: Props) {
  const list = useLocalListState(JOBS_LIST, initial);
  const v = jobsVariables(null, { ...list.state, filters: list.filters });
  const served = withLatestRun(jobs).filter(
    (j) => !v.filter?.owner && (!v.filter?.app || v.filter.app.includes(j.registeredAppSlug))
  );
  const rows = narrowJobs(served, list.filters);
  return <JobsScreen {...jobsProps({ rows, totalCount: served.length, ...patch })} list={list} />;
}

/** All: every cron job with its schedule, concurrency and last run. */
export const Full: Story = { render: () => <Jobs /> };

export const Loading: Story = { render: () => <Jobs jobs={[]} loading /> };

/** No cron job declared anywhere: the manifest reference. */
export const Empty: Story = { render: () => <Jobs jobs={[]} /> };

export const EmptyFiltered: Story = {
  render: () => <Jobs initial={{ filters: { app: "no-such-app" } }} />,
};

export const ErrorState: Story = {
  render: () => <Jobs jobs={[]} error={{ message: "upstream timed out" }} />,
};

/** Failing: jobs whose latest run failed. */
export const Failing: Story = { render: () => <Jobs initial={{ view: "failing" }} /> };

/** Paused: no data behind it yet, so empty, with the note saying why. */
export const Paused: Story = { render: () => <Jobs initial={{ view: "paused" }} /> };

/** Mine: the jobs the viewer owns; none among the fixtures. */
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
