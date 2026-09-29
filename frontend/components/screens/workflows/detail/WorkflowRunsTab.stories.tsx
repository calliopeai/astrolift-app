import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { fromConfiguredRun, WORKFLOW_RUNS_LIST } from "./workflow-runs-list";
import { LONG_WORKFLOW, RUN_RUNNING } from "./workflow-detail-b.fixtures";
import { CONFIGURED_ROWS, RUNS_TAB, SHA } from "./workflow-run.fixtures";
import { WorkflowRunsTab, type WorkflowRunsTabProps } from "./WorkflowRunsTab";

/** A workflow's Runs tab: the shared Runs list, embedded and narrowed (spec 44 §5.1, §5.2). */
const meta: Meta = {
  title: "Screens/Workflows/Detail/WorkflowRunsTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<WorkflowRunsTabProps, "list">> & { initial?: Partial<ListState> };

function Tab({ initial, ...props }: Props) {
  const list = useLocalListState(WORKFLOW_RUNS_LIST, initial);
  return <WorkflowRunsTab {...RUNS_TAB} list={list} {...props} />;
}

/** A definition's runs, with the live one placed on the line above the list. */
export const Full: Story = {
  render: () => <Tab />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Live")).toBeInTheDocument();
    await expect(canvas.getAllByRole("link").length).toBeGreaterThan(0);
  },
};

/** A configured workflow: its runs report no current stage, so there is no live view. */
export const Configured: Story = {
  render: () => <Tab rows={CONFIGURED_ROWS} totalCount={CONFIGURED_ROWS.length} live={null} />,
};

/** New runs arrived while reading: they wait behind the pill. */
export const NewRows: Story = {
  render: () => <Tab newRows={{ count: 2, onReveal: () => {} }} />,
};

/** The newest 100 of the project were searched. */
export const Capped: Story = {
  render: () => (
    <Tab
      approximateCount
      coverage="Searched the newest 100 runs in this workflow's project; filters and search cover those."
    />
  ),
};

export const Loading: Story = {
  render: () => <Tab rows={[]} loading totalCount={null} live={null} />,
};

export const Empty: Story = {
  render: () => <Tab rows={[]} totalCount={0} live={null} />,
};

export const LoadError: Story = {
  render: () => (
    <Tab
      rows={[]}
      totalCount={null}
      live={null}
      error={{ message: "Response not successful: Received status code 502" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => {
    const row = fromConfiguredRun({ ...RUN_RUNNING, guid: SHA }, LONG_WORKFLOW);
    return <Tab rows={[{ ...row, trigger: `webhook:${"z".repeat(120)}` }]} totalCount={1} />;
  },
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Tab />
    </div>
  ),
};
