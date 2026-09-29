import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  AGENT_RUN_ROWS,
  LONG_RUN_ROWS,
  RUN_ROWS,
  RUNS_SCREEN,
  UPCOMING_ROWS,
} from "./runs.fixtures";
import { AGENT_RUNS_LIST, RUNS_LIST } from "./runs-list";
import { RunsScreen, type RunsScreenProps } from "./RunsScreen";

/** Agents › Runs: every agent, workflow and task run in one live list (spec 44 §4.1, §5.1). */
const meta: Meta = {
  title: "Screens/Tasks/RunsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<RunsScreenProps, "list">> & { initial?: Partial<ListState> };

function Runs({ initial, ...props }: Props) {
  const list = useLocalListState(props.embedded ? AGENT_RUNS_LIST : RUNS_LIST, initial);
  return <RunsScreen {...RUNS_SCREEN} list={list} {...props} />;
}

/** Every kind, newest first, with the views as tabs. */
export const Full: Story = { render: () => <Runs /> };

/** New runs arrived while reading: they wait behind the pill. */
export const NewRows: Story = {
  render: () => <Runs newRows={{ count: 3, onReveal: () => {} }} />,
};

/** Selecting a running run offers Cancel in the bulk bar. */
export const BulkCancel: Story = {
  render: () => <Runs />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const boxes = await canvas.findAllByRole("checkbox", { name: /select/i });
    await userEvent.click(boxes[1]);
    await expect(canvas.getByRole("button", { name: /^Cancel/ })).toBeInTheDocument();
  },
};

/** Selecting a finished agent run offers Retry in the bulk bar. */
export const BulkRetry: Story = {
  render: () => <Runs />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const boxes = await canvas.findAllByRole("checkbox", { name: /select/i });
    await userEvent.click(boxes[4]);
    await expect(canvas.getByRole("button", { name: /^Retry 1/ })).toBeEnabled();
  },
};

/** The Scheduled view: the next firings of scheduled agents, soonest first. */
export const ScheduledView: Story = {
  render: () => (
    <Runs
      initial={{ view: "scheduled" }}
      rows={UPCOMING_ROWS}
      nextCursor={null}
      totalCount={UPCOMING_ROWS.length}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByText("Scheduled run").length).toBe(UPCOMING_ROWS.length);
    await expect(canvas.getByRole("columnheader", { name: /Fires/ })).toBeInTheDocument();
  },
};

/** No scheduled agent fires: the empty Scheduled view. */
export const ScheduledEmpty: Story = {
  render: () => <Runs initial={{ view: "scheduled" }} rows={[]} nextCursor={null} totalCount={0} />,
};

/** An agent's Runs tab: the same list embedded, the views in a picker. */
export const EmbeddedAgentTab: Story = {
  render: () => <Runs embedded rows={AGENT_RUN_ROWS} totalCount={AGENT_RUN_ROWS.length} />,
};

export const Loading: Story = {
  render: () => <Runs rows={[]} loading nextCursor={null} totalCount={null} />,
};

export const Empty: Story = {
  render: () => <Runs rows={[]} nextCursor={null} totalCount={0} />,
};

export const EmbeddedEmpty: Story = {
  render: () => <Runs embedded rows={[]} nextCursor={null} totalCount={0} />,
};

export const LoadError: Story = {
  render: () => (
    <Runs
      rows={[]}
      nextCursor={null}
      totalCount={null}
      error={{ message: "Response not successful: Received status code 502" }}
    />
  ),
};

/** A 64-character id, a 200-character ARN, an unbroken URL. */
export const LongStrings: Story = {
  render: () => <Runs rows={LONG_RUN_ROWS} totalCount={LONG_RUN_ROWS.length} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Runs rows={[...LONG_RUN_ROWS, ...RUN_ROWS]} />
    </div>
  ),
};
