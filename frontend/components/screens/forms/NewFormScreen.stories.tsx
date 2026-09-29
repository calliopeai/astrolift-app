import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { NEW_FORM } from "./forms.fixtures";
import { NewFormScreen } from "./NewFormScreen";

const meta: Meta = {
  title: "Screens/Forms/NewFormScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The builder with its default one-field schema and the live preview open. */
export const Full: Story = { render: () => <NewFormScreen {...NEW_FORM} /> };

/**
 * The create page loads nothing, so it has no loading, empty or error screen;
 * the closest error is a missing name on submit.
 */
export const MissingName: Story = {
  render: () => <NewFormScreen {...NEW_FORM} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Create Form/ }));
    await expect(await c.findByText("Name is required")).toBeInTheDocument();
  },
};

export const JsonMode: Story = {
  render: () => <NewFormScreen {...NEW_FORM} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /JSON/ }));
    await expect(canvasElement.querySelector("textarea.font-mono")).not.toBeNull();
  },
};

export const PreviewHidden: Story = {
  render: () => <NewFormScreen {...NEW_FORM} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Hide/ }));
    await expect(c.queryByText("Live Preview")).toBeNull();
  },
};

export const LongStrings: Story = {
  render: () => <NewFormScreen {...NEW_FORM} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.type(
      c.getByPlaceholderText("e.g. Expense Report"),
      "Quarterly platform capacity and regional expansion request for shared production workloads"
    );
  },
};
