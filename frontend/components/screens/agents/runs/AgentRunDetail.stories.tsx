import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LiveLogTerminal } from "@/components/observability/LiveLogTerminal";

import { AgentInteractionMapView } from "./AgentInteractionMap";
import { AgentRunDetail } from "./AgentRunDetail";
import {
  COMPLETED_TASK,
  FAILED_TASK,
  INTERACTION_MAP,
  LOG_LINES,
  LONG_INTERACTIONS,
  LONG_TASK,
  RUN_DETAIL,
  TASK_ID,
} from "./agent-runs.fixtures";

const meta: Meta = {
  title: "Screens/Agents/Runs/AgentRunDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const map = <AgentInteractionMapView {...INTERACTION_MAP} />;
const terminal = (running: boolean) => (
  <LiveLogTerminal
    taskId={TASK_ID}
    running={running}
    lines={LOG_LINES}
    loading={false}
    error={null}
    className="h-[28rem]"
  />
);

export const Running: Story = {
  render: () => (
    <AgentRunDetail {...RUN_DETAIL} interactionMap={map} logTerminal={terminal(true)} />
  ),
};

export const Completed: Story = {
  render: () => (
    <AgentRunDetail
      {...RUN_DETAIL}
      task={COMPLETED_TASK}
      terminal
      interactionMap={
        <AgentInteractionMapView {...INTERACTION_MAP} taskStatus="completed" isTerminal />
      }
      logTerminal={terminal(false)}
    />
  ),
};

/** A spawn-failed run: no pod, no logs, the failure callout on top. */
export const SpawnFailed: Story = {
  render: () => (
    <AgentRunDetail
      {...RUN_DETAIL}
      task={FAILED_TASK}
      terminal
      logs={[]}
      interactionMap={
        <AgentInteractionMapView
          {...INTERACTION_MAP}
          taskStatus="failed"
          isTerminal
          interactions={[]}
        />
      }
    />
  ),
};

export const Loading: Story = {
  render: () => <AgentRunDetail {...RUN_DETAIL} task={null} loading />,
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
      interactionMap={
        <AgentInteractionMapView {...INTERACTION_MAP} interactions={LONG_INTERACTIONS} />
      }
      logTerminal={terminal(true)}
    />
  ),
};
