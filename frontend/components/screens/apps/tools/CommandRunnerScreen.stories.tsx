import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  COMMAND_HISTORY,
  COMMAND_OUTPUT,
  COMMAND_RUNNER,
  LONG,
  T0,
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
      output={[{ channel: "stdout", text: "booting…\n", ts: T0 }]}
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
        { channel: "system", text: "\n[ws: connection error]\n", ts: T0 },
        { channel: "system", text: "\n[ws closed: code 1006]\n", ts: T0 + 20 },
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
      output={[{ channel: "stdout", text: `${LONG} ${LONG} ${LONG}\n`, ts: T0 }]}
      connState="closed"
      exitCode={0}
      history={COMMAND_HISTORY}
    />
  ),
};

/** A chunk that ends mid-line joins the next one into a single log line. */
export const SplitChunks: Story = {
  render: () => (
    <CommandRunnerScreen
      {...COMMAND_RUNNER}
      command="python manage.py showmigrations"
      output={[
        { channel: "stdout", text: "orders\n [X] 0001_init", ts: T0 },
        { channel: "stdout", text: "ial\n [ ] 0002_add_sku\n", ts: T0 + 30 },
        { channel: "system", text: "\n[exit 0]\n", ts: T0 + 60 },
      ]}
      connState="closed"
      exitCode={0}
    />
  ),
};

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <CommandRunnerScreen
        {...COMMAND_RUNNER}
        slug={LONG}
        workloads={[{ id: "wl-long", slug: LONG, kind: "deployment" }]}
        workloadSlug={LONG}
        containers={[{ id: "c-long", name: LONG, isPrimary: true }]}
        containerName={LONG}
        command={`echo ${LONG}`}
        output={COMMAND_OUTPUT}
        connState="closed"
        exitCode={2}
        history={COMMAND_HISTORY}
      />
    </div>
  ),
};
