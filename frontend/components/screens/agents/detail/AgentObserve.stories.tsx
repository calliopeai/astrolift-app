import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentObserveScreen } from "./AgentObserve";
import { AgentTaskLogsView } from "./AgentTaskLogs";
import {
  FAILED_TASK,
  LOG_LINES,
  LONG,
  LONG_LOG_LINES,
  OLDER_TASK,
  RUNNING_TASK,
} from "./agent-observe-secure.fixtures";

// The live theatre is an app/ container with its own queries; the screen
// takes it as a slot, so stories stand in a placeholder.
const theatre = (
  <div className="text-muted-foreground rounded-md border border-dashed p-6 text-sm">
    Live theatre slot (AgentTheatreContainer)
  </div>
);

const meta: Meta<typeof AgentObserveScreen> = {
  title: "Screens/Agents/Detail/AgentObserve",
  component: AgentObserveScreen,
  args: {
    tasks: [RUNNING_TASK, OLDER_TASK],
    latest: RUNNING_TASK,
    loading: false,
    theatre,
    logs: <AgentTaskLogsView task={RUNNING_TASK} lines={LOG_LINES} loading={false} />,
  },
};
export default meta;

type Story = StoryObj<typeof AgentObserveScreen>;

export const Full: Story = {};

export const Loading: Story = { args: { tasks: [], latest: undefined, loading: true } };

export const Empty: Story = { args: { tasks: [], latest: undefined, logs: null } };

/**
 * The tab has no error state (query errors fall through to the empty state);
 * the closest real one is the latest run failing at spawn.
 */
export const SpawnFailed: Story = {
  args: {
    tasks: [FAILED_TASK, RUNNING_TASK],
    latest: FAILED_TASK,
    logs: <AgentTaskLogsView task={FAILED_TASK} lines={[]} loading={false} />,
  },
};

export const LongStrings: Story = {
  args: {
    logs: (
      <AgentTaskLogsView
        task={{ ...RUNNING_TASK, id: LONG, status: LONG }}
        lines={LONG_LOG_LINES}
        loading={false}
      />
    ),
  },
};
