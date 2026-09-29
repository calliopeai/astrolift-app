import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { LONG_TOOLS, MANY_TOOLS, TOOLS } from "./agent-tools.fixtures";
import { selectTools, TOOLS_LIST } from "./tools-list";
import { ToolRegistryScreen, type ToolRegistryScreenProps } from "./ToolRegistryScreen";
import type { ToolRegistryTool } from "./use-tool-registry";

const meta: Meta = {
  title: "Screens/Agents/Tools/ToolRegistryScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<ToolRegistryScreenProps, "list">> & {
  tools?: ToolRegistryTool[];
  initial?: Partial<ListState>;
};

/** The screen over fixture tools, filtered and paged the way the hook does it. */
function Tools({ tools = TOOLS, initial, ...patch }: Props) {
  const list = useLocalListState(TOOLS_LIST, initial);
  const { rows, totalCount } = selectTools(tools, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return (
    <ToolRegistryScreen
      list={list}
      rows={rows}
      totalCount={totalCount}
      loading={false}
      error={null}
      onRetry={() => {}}
      {...patch}
    />
  );
}

/** Newest first, one tool per adapter and one with no adapter recorded. */
export const Full: Story = {
  render: () => <Tools />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const view of ["All", "Mine", "Built-in", "Custom"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
  },
};

export const Loading: Story = { render: () => <Tools tools={[]} loading /> };

export const Empty: Story = { render: () => <Tools tools={[]} /> };

export const LoadError: Story = {
  render: () => <Tools tools={[]} error={{ message: "Network error: 502" }} />,
};

/** Mine lists every tool for now; its note says so. */
export const Mine: Story = { render: () => <Tools initial={{ view: "mine" }} /> };

/** Built-in cannot split tools until the API carries the flag: an empty view with a note. */
export const BuiltIn: Story = { render: () => <Tools initial={{ view: "builtin" }} /> };

export const EmptyFiltered: Story = {
  render: () => <Tools initial={{ filters: { adapter: "mcp_server" }, q: "invoice" }} />,
};

/** Sixty tools: numbered pages, page 2 of 3. */
export const Paged: Story = { render: () => <Tools tools={MANY_TOOLS} initial={{ page: 2 }} /> };

/** A 64-char SHA name, a 200-char ARN description and an unbroken handler URL. */
export const LongStrings: Story = { render: () => <Tools tools={[...LONG_TOOLS, ...TOOLS]} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Tools tools={[...LONG_TOOLS, ...MANY_TOOLS]} />
    </div>
  ),
};
