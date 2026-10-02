import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { agentFleetSnapshot } from "./agent-fleet-snapshot";
import {
  AGENT,
  DETAIL,
  FLEET_AGENTS,
  LONG,
  LONG_AGENT,
  OVERVIEW,
  RUNNING_AGENT,
  RUNNING_TASKS,
  TASKS,
} from "./agent-detail-shell.fixtures";
import { AgentOverviewView } from "./AgentOverview";

/**
 * The agent's Overview (spec 44 §5.2): the latest run and the runtime
 * first, then Recent runs as a ListSummary to Runs, and the agent in the
 * fleet view. The frame above carries Run now and a failure's reason.
 */
const meta: Meta<typeof AgentOverviewView> = {
  title: "Screens/Agents/Detail/AgentOverview",
  component: AgentOverviewView,
  args: OVERVIEW,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof AgentOverviewView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Latest run")).toBeInTheDocument();
    // Three tools: "fetch" is shared by both skills and counted once.
    await expect(canvas.getByRole("link", { name: "3" })).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: /View all/ })).toHaveAttribute(
      "href",
      "/agents/research-scout/runs"
    );
  },
};

export const UppercaseRunning: Story = {
  args: {
    fleet: null,
    runs: { ...OVERVIEW.runs, rows: [{ ...RUNNING_TASKS[0], status: "RUNNING" }] },
  },
};

export const Loading: Story = {
  args: {
    detail: null,
    detailLoading: true,
    fleet: null,
    runs: { ...OVERVIEW.runs, rows: [], count: null, loading: true },
  },
};

/** Never run: no image, brief, skills or tools yet. */
export const Empty: Story = {
  args: {
    detail: { ...DETAIL, imageRef: "", brief: null, skills: [] },
    runs: { ...OVERVIEW.runs, rows: [], count: 0 },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("Not run yet")).toBeInTheDocument();
  },
};

/** Both reads failed with nothing cached: each panel says so, with Retry. */
export const Error: Story = {
  args: {
    detail: null,
    detailError: "Network error: failed to fetch agent",
    runs: {
      ...OVERVIEW.runs,
      rows: [],
      count: null,
      error: "Network error: failed to fetch agentTasksPage",
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getAllByRole("button", { name: "Retry" }).length).toBe(3);
  },
};

/** A failure-heavy history; the reason itself is the frame's, above the tab. */
export const FailedRuns: Story = {
  args: {
    agent: { ...AGENT, lastRunStatus: "failed" },
    runs: {
      ...OVERVIEW.runs,
      rows: TASKS.slice(0, 5).map((t) => ({ ...t, status: "failed" })),
    },
  },
};

/** Running: the overseer input joins the Latest run panel and sends a message. */
export const RunningWithOverseerChat: Story = {
  args: {
    agent: RUNNING_AGENT,
    runs: { ...OVERVIEW.runs, rows: RUNNING_TASKS.slice(0, 5) },
    fleet: agentFleetSnapshot(
      FLEET_AGENTS.map((a) => (a.id === AGENT.id ? RUNNING_AGENT : a)),
      AGENT.id,
      RUNNING_TASKS.slice(0, 5),
      Date.UTC(2026, 8, 28, 14, 0, 0)
    ),
    onSendInput: fn(async () => true),
  },
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Overseer message"), "Focus on pricing pages");
    await userEvent.click(canvas.getByRole("button", { name: "Send" }));
    await expect(args.onSendInput).toHaveBeenCalledWith(
      "0a1b2c3d-7777-4a2b-9c3d-000000000007",
      "Focus on pricing pages"
    );
    await expect(await canvas.findByText("Focus on pricing pages")).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  args: {
    agent: LONG_AGENT,
    detail: { ...DETAIL, imageRef: `ghcr.io/acme/${LONG}@sha256:${"a".repeat(64)}` },
    runs: {
      ...OVERVIEW.runs,
      rows: [{ ...TASKS[0]!, id: `${LONG}-${"f".repeat(64)}` }, ...TASKS.slice(1, 5)],
    },
  },
};

/** The narrowest the web console goes: panels stack, nothing scrolls sideways. */
export const At768: Story = {
  args: LongStrings.args,
  render: (args) => (
    <div style={{ width: 768 }}>
      <AgentOverviewView {...args} />
    </div>
  ),
};

export const JapaneseInputRefused: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <AgentOverviewView
        {...OVERVIEW}
        fleet={null}
        runs={{ ...OVERVIEW.runs, rows: RUNNING_TASKS }}
        onSendInput={async () => false}
      />
    </NextIntlClientProvider>
  ),
};
