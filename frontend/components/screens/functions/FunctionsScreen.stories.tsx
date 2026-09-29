import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import {
  APP_WORKLOAD,
  AREA,
  LONG_WORKLOADS,
  type AreaWorkload,
  MANY_WORKLOADS,
  serveWorkloads,
} from "@/components/screens/workloads/workloads.fixtures";

import { FUNCTIONS_LIST } from "./functions-list";
import { FunctionsScreen, type FunctionsScreenProps } from "./FunctionsScreen";

const meta: Meta = {
  title: "Screens/Functions/FunctionsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<FunctionsScreenProps, "list">> & {
  workloads?: AreaWorkload[];
  initial?: Partial<ListState>;
};

/** The screen over fixture workloads, narrowed and paged the way the server does it. */
function Functions({ workloads = AREA, initial, ...patch }: Props) {
  const list = useLocalListState(FUNCTIONS_LIST, initial);
  const { rows, totalCount } = serveWorkloads(
    workloads,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    ["function"]
  );
  return (
    <FunctionsScreen
      list={list}
      rows={rows}
      totalCount={totalCount}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      {...patch}
    />
  );
}

/** Only the function workloads; the agent and workflow ones are Workloads' to show. */
export const Full: Story = {
  render: () => <Functions />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Thumbnailer")).toBeInTheDocument();
    await expect(canvas.queryByText("Support bot")).not.toBeInTheDocument();
    for (const view of ["All", "Mine"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
  },
};

export const Loading: Story = { render: () => <Functions workloads={[]} loading /> };

/** No function workloads, only an app's own deployment. */
export const Empty: Story = {
  render: () => <Functions workloads={[APP_WORKLOAD]} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("No function workloads")).toBeInTheDocument();
  },
};

export const LoadError: Story = {
  render: () => <Functions workloads={[]} error={{ message: "Network error: 502" }} />,
};

export const Mine: Story = { render: () => <Functions initial={{ view: "mine" }} /> };

export const EmptyFiltered: Story = {
  render: () => <Functions initial={{ filters: { app: "no-such-app" } }} />,
};

export const Paged: Story = {
  render: () => <Functions workloads={MANY_WORKLOADS} initial={{ pageSize: 25 }} />,
};

export const LongStrings: Story = {
  render: () => <Functions workloads={[...LONG_WORKLOADS, ...AREA]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Functions workloads={[...LONG_WORKLOADS, ...MANY_WORKLOADS]} />
    </div>
  ),
};
