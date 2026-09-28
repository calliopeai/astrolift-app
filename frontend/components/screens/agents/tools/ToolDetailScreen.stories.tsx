import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DETAIL, LONG_TOOL } from "./agent-tools.fixtures";
import { ToolDetailScreen } from "./ToolDetailScreen";

const meta: Meta = {
  title: "Screens/Agents/Tools/ToolDetailScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <ToolDetailScreen {...DETAIL} />,
};

export const Loading: Story = {
  render: () => <ToolDetailScreen {...DETAIL} tool={null} loading />,
};

/**
 * The detail screen has no empty list; the closest state is a tool id that
 * does not exist (deleted, or no access).
 */
export const NotFound: Story = {
  render: () => <ToolDetailScreen {...DETAIL} tool={null} />,
};

export const LoadError: Story = {
  render: () => (
    <ToolDetailScreen {...DETAIL} tool={null} error={{ message: "Network error: 502" }} />
  ),
};

/** Delete in flight: the header action shows a spinner and is disabled. */
export const Deleting: Story = {
  render: () => <ToolDetailScreen {...DETAIL} deleting />,
};

export const LongStrings: Story = {
  render: () => <ToolDetailScreen {...DETAIL} tool={LONG_TOOL} />,
};
