import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { JOB_RUNS, JOB_RUN_DETAIL, LONG_FAILED_RUN, LONG_JOB_RUNS } from "./jobs-tasks.fixtures";
import { JobRunDetail } from "./JobRunDetail";

const meta: Meta = {
  title: "Screens/Jobs/JobRunDetail",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Finished: both phases done, the output in the log. */
export const Full: Story = { render: () => <JobRunDetail {...JOB_RUN_DETAIL} /> };

/** Still running: the run step pulses and the clock runs. */
export const Running: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} id={JOB_RUNS[0].id} run={JOB_RUNS[0]} />,
};

/** Non-zero exit: the exit code and last line first. */
export const Failed: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} id={JOB_RUNS[2].id} run={JOB_RUNS[2]} />,
};

export const Loading: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} loading run={null} />,
};

/** No run with this id, or no permission to see it. */
export const NotFound: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} run={null} />,
};

export const ErrorState: Story = {
  render: () => (
    <JobRunDetail {...JOB_RUN_DETAIL} run={null} error={{ message: "upstream timed out" }} />
  ),
};

/** No output captured. */
export const Empty: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} run={{ ...JOB_RUNS[1], output: "" }} />,
};

export const LongStrings: Story = {
  render: () => (
    <JobRunDetail {...JOB_RUN_DETAIL} id={LONG_JOB_RUNS[0].id} run={LONG_JOB_RUNS[0]} />
  ),
};

/** A 200-character ARN and an unbroken URL as the failure reason. */
export const LongFailure: Story = {
  render: () => <JobRunDetail {...JOB_RUN_DETAIL} id={LONG_FAILED_RUN.id} run={LONG_FAILED_RUN} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <JobRunDetail {...JOB_RUN_DETAIL} id={JOB_RUNS[2].id} run={JOB_RUNS[2]} />
    </div>
  ),
};
