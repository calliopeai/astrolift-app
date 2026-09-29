import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { AgentConfigFormBuilder } from "./AgentConfigForm";
import {
  AGENT_ERRORS,
  BUILDER_PROPS,
  EMPTY_AGENT_MODEL,
  INVALID_AGENT_MODEL,
  LONG_AGENT_MODEL,
} from "./app-config-agent.fixtures";

const meta: Meta<typeof AgentConfigFormBuilder> = {
  title: "Screens/Apps/Config/AgentConfigForm",
  component: AgentConfigFormBuilder,
  args: BUILDER_PROPS,
};
export default meta;

type Story = StoryObj<typeof AgentConfigFormBuilder>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByText("triage").length).toBeGreaterThan(0);
  },
};

/** A fresh agent config: no skills, no tools. The builder has no loading state; the pane parses synchronously. */
export const Empty: Story = { args: { model: EMPTY_AGENT_MODEL } };

/** Validation errors: missing version, duplicate skill slug, bad JSON schema. */
export const Invalid: Story = { args: { model: INVALID_AGENT_MODEL, errors: AGENT_ERRORS } };

export const LongStrings: Story = { args: { model: LONG_AGENT_MODEL } };
