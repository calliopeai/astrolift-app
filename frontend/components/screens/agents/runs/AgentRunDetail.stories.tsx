import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentRunDetail } from "./AgentRunDetail";
import {
  COMPLETED_TASK,
  FAILED_TASK,
  INTERACTION_MAP,
  LONG_INTERACTIONS,
  LONG_TASK,
  MANY_INTERACTIONS,
  RUN_DETAIL,
} from "./agent-runs.fixtures";

/** One agent run on the run page: Timeline, Log, then Details and Result (spec 44 §5.5). */
const meta: Meta = {
  title: "Screens/Agents/Runs/AgentRunDetail",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Running: Story = {
  render: () => <AgentRunDetail {...RUN_DETAIL} />,
};

export const Completed: Story = {
  render: () => (
    <AgentRunDetail
      {...RUN_DETAIL}
      task={COMPLETED_TASK}
      terminal
      interactions={{ ...INTERACTION_MAP, taskStatus: "completed", isTerminal: true }}
    />
  ),
};

/** A spawn-failed run: no pod, no logs, the reason first in the Timeline. */
export const SpawnFailed: Story = {
  render: () => (
    <AgentRunDetail
      {...RUN_DETAIL}
      task={FAILED_TASK}
      terminal
      logs={[]}
      interactions={{
        ...INTERACTION_MAP,
        taskStatus: "failed",
        isTerminal: true,
        interactions: [],
      }}
    />
  ),
};

/** Sixty tool calls: the Timeline keeps the newest and folds the rest into one line. */
export const ManyToolCalls: Story = {
  render: () => (
    <AgentRunDetail
      {...RUN_DETAIL}
      interactions={{ ...INTERACTION_MAP, interactions: MANY_INTERACTIONS }}
    />
  ),
};

export const Loading: Story = {
  render: () => <AgentRunDetail {...RUN_DETAIL} task={null} loading />,
};

/** The log is still loading while the run itself is known. */
export const LogLoading: Story = {
  render: () => <AgentRunDetail {...RUN_DETAIL} logs={[]} logsLoading />,
};

export const LogError: Story = {
  render: () => (
    <AgentRunDetail {...RUN_DETAIL} logs={[]} logsError="agentTaskLogs: upstream timed out" />
  ),
};

/** No run with this id, or no access to it (the closest thing to empty). */
export const NotFound: Story = {
  render: () => <AgentRunDetail {...RUN_DETAIL} task={null} />,
};

export const LoadError: Story = {
  render: () => (
    <AgentRunDetail
      {...RUN_DETAIL}
      task={null}
      error="Response not successful: Received status code 502"
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AgentRunDetail
      {...RUN_DETAIL}
      task={LONG_TASK}
      interactions={{ ...INTERACTION_MAP, interactions: LONG_INTERACTIONS }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <AgentRunDetail
        {...RUN_DETAIL}
        task={LONG_TASK}
        interactions={{ ...INTERACTION_MAP, interactions: LONG_INTERACTIONS }}
      />
    </div>
  ),
};
