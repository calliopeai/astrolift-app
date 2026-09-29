import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { AgentConfigFormPane } from "./AgentConfigFormPane";
import {
  INVALID_AGENT_TOML,
  LONG_AGENT_TOML,
  PANE_PROPS,
  UNSAFE_AGENT_TOML,
} from "./app-config-agent.fixtures";

const meta: Meta<typeof AgentConfigFormPane> = {
  title: "Screens/Apps/Config/AgentConfigFormPane",
  component: AgentConfigFormPane,
  args: PANE_PROPS,
};
export default meta;

type Story = StoryObj<typeof AgentConfigFormPane>;

export const Full: Story = {};

/** An empty astrolift.toml parses to an empty model. There is no loading state: the draft is parsed synchronously. */
export const Empty: Story = { args: { draft: "" } };

/** The draft parses but fails validation; the preview lists the errors. */
export const Invalid: Story = { args: { draft: INVALID_AGENT_TOML } };

/** TOML the scoped codec cannot round-trip: points the operator at the Code view. */
export const Unsafe: Story = {
  args: { draft: UNSAFE_AGENT_TOML },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button")).toBeInTheDocument();
  },
};

export const LongStrings: Story = { args: { draft: LONG_AGENT_TOML } };
