import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentTaskLogsView } from "./AgentTaskLogs";
import {
  FAILED_TASK,
  LOG_LINES,
  LONG,
  LONG_LOG_LINES,
  RUNNING_TASK,
} from "./agent-observe-secure.fixtures";

const meta: Meta<typeof AgentTaskLogsView> = {
  title: "Screens/Agents/Detail/AgentTaskLogs",
  component: AgentTaskLogsView,
  args: { task: RUNNING_TASK, lines: LOG_LINES, loading: false },
};
export default meta;

type Story = StoryObj<typeof AgentTaskLogsView>;

export const Full: Story = {};

export const Loading: Story = { args: { lines: [], loading: true } };

/** #891: the run is fine, the log relay has not delivered lines yet. */
export const Empty: Story = { args: { lines: [] } };

/** Spawn failure: no pod started, so the failure message is the only signal. */
export const SpawnFailed: Story = { args: { task: FAILED_TASK, lines: [] } };

export const LongStrings: Story = {
  args: {
    task: { ...FAILED_TASK, id: LONG, status: "failed", failureMessage: LONG.repeat(3) },
    lines: LONG_LOG_LINES,
  },
};
