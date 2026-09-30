import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fn } from "storybook/test";

import { agentLogLines } from "./agent-log-lines";
import {
  FAILED_TASK,
  LOG_LINES,
  LONG,
  LONG_LOG_LINES,
  RUNNING_TASK,
} from "./agent-observe-secure.fixtures";
import { AgentTaskLogsView } from "./AgentTaskLogs";

/** One run's log tail on LogView: follows the end, scrolls in its pane, downloads. */
const meta: Meta<typeof AgentTaskLogsView> = {
  title: "Screens/Agents/Detail/AgentTaskLogs",
  component: AgentTaskLogsView,
  args: {
    task: RUNNING_TASK,
    lines: agentLogLines(LOG_LINES),
    loading: false,
    error: null,
    onRetry: fn(),
    onDownload: fn(),
    onLoadEarlier: fn(),
    onRefresh: fn(),
    hasMore: false,
    loadingEarlier: false,
    pageError: null,
    liveOnly: true,
    windowLimited: false,
  },
};
export default meta;

type Story = StoryObj<typeof AgentTaskLogsView>;

export const Full: Story = {};

export const EarlierPages: Story = { args: { hasMore: true, windowLimited: true } };
export const LoadingEarlier: Story = { args: { hasMore: true, loadingEarlier: true } };
export const ExpiredPage: Story = {
  args: { hasMore: true, pageError: "Task log page expired; refresh the log." },
};

export const Loading: Story = { args: { lines: [], loading: true } };

/** #891: the run is fine, the log relay has not delivered lines yet. */
export const Empty: Story = { args: { lines: [] } };

export const Error: Story = {
  args: { lines: [], error: "Network error: failed to fetch agentTaskLogs" },
};

/** Spawn failure: no pod started, so the failure message is the only signal. */
export const SpawnFailed: Story = { args: { task: FAILED_TASK, lines: [] } };

export const LongStrings: Story = {
  args: {
    task: { ...FAILED_TASK, id: LONG, status: "failed", failureMessage: LONG.repeat(3) },
    lines: agentLogLines(LONG_LOG_LINES),
  },
};

export const At768: Story = {
  args: LongStrings.args,
  render: (args) => (
    <div style={{ width: 768 }}>
      <AgentTaskLogsView {...args} />
    </div>
  ),
};
