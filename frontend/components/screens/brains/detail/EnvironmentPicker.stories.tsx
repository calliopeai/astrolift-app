import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { EnvironmentPicker } from "./EnvironmentPicker";

const meta: Meta<typeof EnvironmentPicker> = {
  title: "Screens/Brains/Detail/EnvironmentPicker",
  component: EnvironmentPicker,
  parameters: { layout: "padded" },
  args: {
    environments: [
      { name: "production", href: "?env=production", active: true },
      { name: "staging", href: "?env=staging", active: false },
      { name: "eu-west-review", href: "?env=eu-west-review", active: false },
    ],
  },
};
export default meta;

type Story = StoryObj<typeof EnvironmentPicker>;

export const Three: Story = {
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("link", { name: "production" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

/** One environment: nothing to pick. */
export const One: Story = {
  args: { environments: [{ name: "production", href: "?env=production", active: true }] },
};
