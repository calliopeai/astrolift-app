import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  COMMAND_HISTORY,
  COMMAND_OUTPUT,
  COMMAND_RUNNER,
  LONG,
} from "./app-observability-shell-topology-commands.fixtures";
import { CommandRunnerScreen } from "./CommandRunnerScreen";

const meta: Meta = {
  title: "Screens/Apps/Tools/CommandRunnerScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** A finished run with stdout, stderr and a non-zero exit. */
export const Full: Story = {
  render: () => (
    <CommandRunnerScreen
      {...COMMAND_RUNNER}
      command="ls -la /tmp /tmp/missing"
      output={COMMAND_OUTPUT}
      connState="closed"
      exitCode={2}
      history={COMMAND_HISTORY}
    />
  ),
};

/**
 * The runner has no page-level loading state; the closest is workloads not
 * yet loaded (empty pickers).
 */
export const Loading: Story = {
  render: () => (
    <CommandRunnerScreen
      {...COMMAND_RUNNER}
      workloads={[]}
      workloadSlug=""
      containers={[]}
      containerName=""
    />
  ),
};

/** Nothing run yet, no history. */
export const Empty: Story = {
  render: () => <CommandRunnerScreen {...COMMAND_RUNNER} />,
};

export const Running: Story = {
  render: () => (
    <CommandRunnerScreen
      {...COMMAND_RUNNER}
      command="tail -f /var/log/app.log"
      output={[{ channel: "stdout", text: "booting…\n" }]}
      connState="open"
      running
    />
  ),
};

/** Connection failure, shown inline as system output. */
export const ConnectionError: Story = {
  render: () => (
    <CommandRunnerScreen
      {...COMMAND_RUNNER}
      command="ls"
      output={[
        { channel: "system", text: "\n[ws: connection error]\n" },
        { channel: "system", text: "\n[ws closed: code 1006]\n" },
      ]}
      connState="closed"
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <CommandRunnerScreen
      {...COMMAND_RUNNER}
      slug={LONG}
      workloads={[{ id: "wl-long", slug: LONG, kind: "deployment" }]}
      workloadSlug={LONG}
      containers={[{ id: "c-long", name: LONG, isPrimary: true }]}
      containerName={LONG}
      command={`echo ${LONG}`}
      output={[{ channel: "stdout", text: `${LONG} ${LONG} ${LONG}\n` }]}
      connState="closed"
      exitCode={0}
      history={COMMAND_HISTORY}
    />
  ),
};
