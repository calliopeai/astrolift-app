import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { EDITOR, LONG_TRIGGERS } from "./agent-trigger-editor.fixtures";
import { TriggerBindingEditorView } from "./TriggerBindingEditor";

const meta: Meta<typeof TriggerBindingEditorView> = {
  title: "Screens/Agents/Detail/TriggerBindingEditor",
  component: TriggerBindingEditorView,
  args: EDITOR,
};
export default meta;

type Story = StoryObj<typeof TriggerBindingEditorView>;

export const Full: Story = {};

export const Loading: Story = {
  args: { triggers: [], loading: true },
};

export const Empty: Story = {
  args: { triggers: [] },
};

/**
 * The editor has no query-error state (a failed list read falls back to the
 * empty list); the real error it shows is an invalid input mapping.
 */
export const InvalidMapping: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const mapping = canvas.getByLabelText("Input mapping (optional)");
    await userEvent.type(mapping, "{{not json");
    await userEvent.tab();
    await expect(await canvas.findByText("Invalid JSON — check syntax.")).toBeInTheDocument();
  },
};

/** A bind that succeeds shows the one-time signing-secret reveal. */
export const Bound: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Source repo"), "acme/api");
    await userEvent.click(canvas.getByRole("button", { name: "Bind trigger" }));
    await expect(
      await canvas.findByText("Trigger bound — save the signing secret")
    ).toBeInTheDocument();
  },
};

export const Creating: Story = {
  args: { creating: true },
};

export const LongStrings: Story = {
  args: { agentName: "A deliberately long agent name that keeps going", triggers: LONG_TRIGGERS },
};
