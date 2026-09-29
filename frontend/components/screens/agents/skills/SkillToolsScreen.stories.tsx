import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { LONG_TOOLS, MANY_TOOLS, serveSkillTools, SKILL, TOOLS } from "./agent-skills.fixtures";
import { SKILL_TOOLS_LIST } from "./skill-tools-list";
import { SkillToolsScreen, type SkillToolsScreenProps } from "./SkillToolsScreen";
import type { ToolDef } from "./use-skill-tool-defs";

const meta: Meta = {
  title: "Screens/Agents/Skills/SkillToolsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<SkillToolsScreenProps, "list">> & {
  tools?: ToolDef[];
  initial?: Partial<ListState>;
};

/** The tab over fixture tools, filtered and paged the way the server does it. */
function Tools({ tools = TOOLS, initial, ...patch }: Props) {
  const list = useLocalListState(SKILL_TOOLS_LIST, initial);
  const { rows, totalCount } = serveSkillTools(tools, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return (
    <SkillToolsScreen
      id="sk-1"
      skill={SKILL}
      skillLoading={false}
      skillError={null}
      onSkillRetry={() => {}}
      list={list}
      rows={rows}
      totalCount={totalCount}
      loading={false}
      error={null}
      onRetry={() => {}}
      removing={false}
      removeTool={async () => {}}
      {...patch}
    />
  );
}

/** One tool per adapter; one without a description, one without a handler ref. */
export const Full: Story = {
  render: () => <Tools />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Register tool" })).toHaveAttribute(
      "href",
      "/agents/skills/sk-1/tools/new"
    );
    await expect(canvas.getByRole("link", { name: "Tools" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

export const Loading: Story = { render: () => <Tools tools={[]} loading /> };

export const Empty: Story = { render: () => <Tools tools={[]} /> };

export const LoadError: Story = {
  render: () => <Tools tools={[]} error={{ message: "Network error: 502" }} />,
};

/** The skill itself failed to load: the frame says so and the list is not drawn. */
export const SkillMissing: Story = { render: () => <Tools skill={null} /> };

export const EmptyFiltered: Story = {
  render: () => <Tools initial={{ q: "no-such-tool" }} />,
};

/** Forty tools: numbered pages. */
export const Paged: Story = { render: () => <Tools tools={MANY_TOOLS} initial={{ page: 2 }} /> };

/** A 64-char SHA handler, a 200-char ARN description, an unbroken URL. */
export const LongStrings: Story = { render: () => <Tools tools={LONG_TOOLS} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Tools tools={[...LONG_TOOLS, ...MANY_TOOLS]} />
    </div>
  ),
};
