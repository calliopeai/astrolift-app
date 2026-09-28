import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { LiveLogTerminal } from "@/components/observability/LiveLogTerminal";

import {
  LOG_LINES,
  RUN,
  RUN_DISPATCH_FAILS,
  RUN_DISPATCHING,
  RUN_EMPTY,
  RUN_LOADING,
  RUN_LONG,
} from "./agent-build-run.fixtures";
import { AgentRunScreen } from "./AgentRunScreen";

const meta: Meta = {
  title: "Screens/Agents/Detail/AgentRunScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const renderLogs = (taskId: string, running: boolean) => (
  <LiveLogTerminal
    taskId={taskId}
    running={running}
    lines={LOG_LINES}
    error={null}
    loading={false}
    className="min-h-0 flex-1"
  />
);

/** Every run state: a VNC run (Watch live), a headless run (Watch logs), queued and finished runs. */
export const Full: Story = { render: () => <AgentRunScreen {...RUN} renderLogs={renderLogs} /> };

export const Loading: Story = {
  render: () => <AgentRunScreen {...RUN_LOADING} renderLogs={renderLogs} />,
};

export const Empty: Story = {
  render: () => <AgentRunScreen {...RUN_EMPTY} renderLogs={renderLogs} />,
};

export const Dispatching: Story = {
  render: () => <AgentRunScreen {...RUN_DISPATCHING} renderLogs={renderLogs} />,
};

/**
 * The screen has no error state of its own: a failed dispatch is a toast
 * raised by the hook, and the list stays as it was.
 */
export const DispatchFails: Story = {
  render: () => <AgentRunScreen {...RUN_DISPATCH_FAILS} renderLogs={renderLogs} />,
};

export const LongStrings: Story = {
  render: () => <AgentRunScreen {...RUN_LONG} renderLogs={renderLogs} />,
};

/** Opening a headless run's log tail. */
export const WatchLogs: Story = {
  render: () => <AgentRunScreen {...RUN} renderLogs={renderLogs} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /watch logs/i }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Live agent logs")).toBeInTheDocument();
  },
};
