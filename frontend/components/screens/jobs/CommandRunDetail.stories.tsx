import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { COMMAND_RUNS, COMMAND_RUN_DETAIL, LONG_COMMAND_RUNS } from "./jobs-tasks.fixtures";
import { CommandRunDetail } from "./CommandRunDetail";

const meta: Meta = {
  title: "Screens/Jobs/CommandRunDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <CommandRunDetail {...COMMAND_RUN_DETAIL} />,
};

/** Still running: no exit code, no end time. */
export const Running: Story = {
  render: () => (
    <CommandRunDetail {...COMMAND_RUN_DETAIL} id={COMMAND_RUNS[0].id} run={COMMAND_RUNS[0]} />
  ),
};

/** Non-zero exit. */
export const Failed: Story = {
  render: () => (
    <CommandRunDetail {...COMMAND_RUN_DETAIL} id={COMMAND_RUNS[2].id} run={COMMAND_RUNS[2]} />
  ),
};

export const Loading: Story = {
  render: () => <CommandRunDetail {...COMMAND_RUN_DETAIL} loading run={null} />,
};

/**
 * No run with this id. The detail has no separate error state: a failed
 * fetch with nothing cached resolves to this same not-found view.
 */
export const NotFound: Story = {
  render: () => <CommandRunDetail {...COMMAND_RUN_DETAIL} run={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <CommandRunDetail
      {...COMMAND_RUN_DETAIL}
      id={LONG_COMMAND_RUNS[0].id}
      run={LONG_COMMAND_RUNS[0]}
    />
  ),
};
