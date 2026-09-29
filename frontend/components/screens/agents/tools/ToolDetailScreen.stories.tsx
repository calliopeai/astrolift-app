import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { DETAIL, LONG_TOOL } from "./agent-tools.fixtures";
import { ToolDetailScreen } from "./ToolDetailScreen";

const meta: Meta = {
  title: "Screens/Agents/Tools/ToolDetailScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Identity, handler and the two schemas on Panels; Save waits for an edit. */
export const Full: Story = {
  render: () => <ToolDetailScreen {...DETAIL} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Save tool" })).toBeDisabled();
    await expect(canvas.getByRole("link", { name: "Tools" })).toHaveAttribute(
      "href",
      "/agents/tools"
    );
  },
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

/** A schema that does not parse, and a refusal the server tied to the slug. */
export const FieldErrors: Story = {
  render: () => (
    <ToolDetailScreen
      {...DETAIL}
      initialErrors={{
        inputSchema: "Input schema is not valid JSON.",
        slug: "A tool with slug lookup-customer already exists on this skill.",
      }}
    />
  ),
};

export const FormError: Story = {
  render: () => (
    <ToolDetailScreen {...DETAIL} initialErrors={{ form: "The tool was not saved." }} />
  ),
};

/** Delete in flight: the `⋯` item is disabled. */
export const Deleting: Story = {
  render: () => <ToolDetailScreen {...DETAIL} deleting />,
};

export const LongStrings: Story = {
  render: () => <ToolDetailScreen {...DETAIL} tool={LONG_TOOL} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ToolDetailScreen {...DETAIL} tool={LONG_TOOL} />
    </div>
  ),
};
