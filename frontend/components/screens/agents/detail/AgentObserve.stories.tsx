import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, within } from "storybook/test";

import { agentLogLines } from "./agent-log-lines";
import {
  FAILED_TASK,
  LOG_LINES,
  LONG_LOG_LINES,
  RUNNING_TASK,
} from "./agent-observe-secure.fixtures";
import { AgentObserveScreen } from "./AgentObserve";
import { AgentTaskLogsView } from "./AgentTaskLogs";

const logsOf = (task: typeof RUNNING_TASK, lines: string[]) => (
  <AgentTaskLogsView
    task={task}
    lines={agentLogLines(lines)}
    loading={false}
    error={null}
    onRetry={() => {}}
    onDownload={() => {}}
    onLoadEarlier={() => {}}
    onRefresh={() => {}}
    hasMore={false}
    loadingEarlier={false}
    pageError={null}
    liveOnly
    windowLimited={false}
  />
);

/** Logs & metrics › Live runs: this agent's newest run and its log. */
const meta: Meta<typeof AgentObserveScreen> = {
  title: "Screens/Agents/Detail/AgentObserve",
  component: AgentObserveScreen,
  args: {
    slug: "research-scout",
    latest: RUNNING_TASK,
    loading: false,
    error: null,
    onRetry: fn(),
    logs: logsOf(RUNNING_TASK, LOG_LINES),
  },
};
export default meta;

type Story = StoryObj<typeof AgentObserveScreen>;

export const Full: Story = {};

export const Loading: Story = { args: { latest: null, loading: true, logs: null } };

export const Empty: Story = {
  args: { latest: null, logs: null },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("No runs yet")).toBeInTheDocument();
  },
};

export const Error: Story = {
  args: { latest: null, logs: null, error: "Network error: failed to fetch agentTasksPage" },
};

/** The newest run failed at spawn: its failure message stands in the log pane. */
export const SpawnFailed: Story = {
  args: { latest: FAILED_TASK, logs: logsOf(FAILED_TASK, []) },
};

export const LongStrings: Story = { args: { logs: logsOf(RUNNING_TASK, LONG_LOG_LINES) } };

export const FrenchEmpty: Story = {
  args: { latest: null, logs: null },
  render: (args) => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <AgentObserveScreen {...args} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseReadError: Story = {
  args: { latest: null, logs: null, error: "RAW_OBSERVE_DIAGNOSTIC" },
  render: (args) => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <AgentObserveScreen {...args} />
    </NextIntlClientProvider>
  ),
};
