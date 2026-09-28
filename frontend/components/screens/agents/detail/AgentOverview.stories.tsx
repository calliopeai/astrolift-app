import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import {
  DETAIL,
  LONG,
  LONG_AGENT,
  OVERVIEW,
  RUNNING_AGENT,
  RUNNING_TASKS,
} from "./agent-detail-shell.fixtures";
import { AgentOverviewView } from "./AgentOverview";

const meta: Meta<typeof AgentOverviewView> = {
  title: "Screens/Agents/Detail/AgentOverview",
  component: AgentOverviewView,
  args: OVERVIEW,
};
export default meta;

type Story = StoryObj<typeof AgentOverviewView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("3 tools")).toBeInTheDocument();
    await expect(canvas.getByText("3 completed")).toBeInTheDocument();
  },
};

/** The detail join is still loading: the activity graph shows a skeleton. */
export const Loading: Story = {
  args: { detail: null, detailLoading: true, tasks: [] },
};

/** Never run, no image, brief, skills or tools. */
export const Empty: Story = {
  args: { detail: { ...DETAIL, imageRef: "", brief: null, skills: [] }, tasks: [] },
};

/**
 * The Overview has no error state (failed reads render as empty). The closest
 * real state is a failure-heavy run history.
 */
export const FailedRuns: Story = {
  args: {
    tasks: OVERVIEW.tasks.map((t) => ({ ...t, status: "failed" })),
  },
};

/** Running: overseer chat connects to the running task and sends a message. */
export const RunningWithOverseerChat: Story = {
  args: { agent: RUNNING_AGENT, tasks: RUNNING_TASKS, onSendInput: fn(async () => true) },
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Connected to running task")).toBeInTheDocument();
    await userEvent.type(canvas.getByLabelText("Overseer message"), "Focus on pricing pages");
    await userEvent.click(canvas.getByRole("button", { name: "Send" }));
    await expect(args.onSendInput).toHaveBeenCalledWith(
      "0a1b2c3d-7777-4a2b-9c3d-000000000007",
      "Focus on pricing pages"
    );
    await expect(await canvas.findByText("Focus on pricing pages")).toBeInTheDocument();
  },
};

export const Dispatching: Story = {
  args: { dispatching: true },
};

export const LongStrings: Story = {
  args: {
    agent: LONG_AGENT,
    detail: { ...DETAIL, imageRef: `ghcr.io/acme/${LONG}:1.4.2-${LONG}` },
  },
};
