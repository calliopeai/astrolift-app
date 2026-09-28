import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { JOB_RUNS, JOB_RUN_DETAIL, LONG_JOB_RUNS } from "./jobs-tasks.fixtures";
import { JobRunDetail } from "./JobRunDetail";

const meta: Meta = {
  title: "Screens/Jobs/JobRunDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} />,
};

/** Still running: no exit code, no end time. */
export const Running: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} id={JOB_RUNS[0].id} run={JOB_RUNS[0]} />,
};

/** Non-zero exit. */
export const Failed: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} id={JOB_RUNS[2].id} run={JOB_RUNS[2]} />,
};

export const Loading: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} loading run={null} />,
};

/**
 * No run with this id. The detail has no separate error state: a failed
 * fetch with nothing cached resolves to this same not-found view.
 */
export const NotFound: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} run={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <JobRunDetail {...JOB_RUN_DETAIL} id={LONG_JOB_RUNS[0].id} run={LONG_JOB_RUNS[0]} />
  ),
};
