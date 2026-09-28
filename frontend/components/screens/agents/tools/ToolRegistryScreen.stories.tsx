import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_TOOLS, REGISTRY } from "./agent-tools.fixtures";
import { ToolRegistryScreen } from "./ToolRegistryScreen";

const meta: Meta = {
  title: "Screens/Agents/Tools/ToolRegistryScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Tools grouped by adapter, including one with no adapter ("unknown"). */
export const Full: Story = {
  render: () => <ToolRegistryScreen {...REGISTRY} />,
};

export const Loading: Story = {
  render: () => <ToolRegistryScreen {...REGISTRY} tools={[]} loading />,
};

export const Empty: Story = {
  render: () => <ToolRegistryScreen {...REGISTRY} tools={[]} />,
};

export const LoadError: Story = {
  render: () => (
    <ToolRegistryScreen {...REGISTRY} tools={[]} error={{ message: "Network error: 502" }} />
  ),
};

export const LongStrings: Story = {
  render: () => <ToolRegistryScreen {...REGISTRY} tools={LONG_TOOLS} />,
};
