import { NextIntlClientProvider, useTranslations } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { LONG_TOOLS, MANY_TOOLS, serveTools, TOOLS } from "./agent-tools.fixtures";
import { localizedToolsList } from "./tools-list";
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

/** The screen over fixture tools, filtered and paged the way the server does it. */
function Tools({ tools = TOOLS, initial, ...patch }: Props) {
  const t = useTranslations("agentToolRegistry");
  const list = useLocalListState(localizedToolsList(t), initial);
  const { rows, totalCount } = serveTools(tools, {
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
      stale={false}
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

/** Mine: the tools the viewer registered. */
export const Mine: Story = { render: () => <Tools initial={{ view: "mine" }} /> };

/** Built-in: the tools that ship with the platform. */
export const BuiltIn: Story = { render: () => <Tools initial={{ view: "builtin" }} /> };

/** Custom: the tools the organization registered on its own skills. */
export const Custom: Story = { render: () => <Tools initial={{ view: "custom" }} /> };

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

export const FrenchFull: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <Tools />
    </NextIntlClientProvider>
  ),
};
export const FrenchMine: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <Tools initial={{ view: "mine" }} />
    </NextIntlClientProvider>
  ),
};
export const FrenchEmpty: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <Tools tools={[]} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseReadFailed: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <Tools tools={[]} error={{ message: "RAW_TOOL_READ_DIAGNOSTIC" }} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseUnknownAdapter: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <Tools tools={[{ ...TOOLS[0], adapter: "__proto__", createdAt: "RAW_INVALID_DATE" }]} />
    </NextIntlClientProvider>
  ),
};
