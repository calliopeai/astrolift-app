import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { ModelAddChoicePanel } from "./ModelAddChoicePanel";

const meta = {
  title: "Screens/Models/ModelAddChoicePanel",
  component: ModelAddChoicePanel,
  parameters: { layout: "padded" },
  args: {
    hosting: { allowed: true, reason: null },
    connection: { allowed: true, reason: null },
  },
} satisfies Meta<typeof ModelAddChoicePanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Allowed: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Host a model" })).toHaveAttribute(
      "href",
      "/models/deploy"
    );
    await expect(canvas.getByRole("link", { name: "Connect a cloud model" })).toHaveAttribute(
      "href",
      "/models/connect"
    );
  },
};
export const Checking: Story = {
  args: {
    hosting: { allowed: null, reason: null },
    connection: { allowed: null, reason: null },
  },
};
export const CloudDisabled: Story = {
  args: {
    connection: {
      allowed: false,
      reason: "Native model connections are disabled by the operator.",
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Connect a cloud model" })).toBeDisabled();
    await expect(
      canvas.queryByRole("link", { name: "Connect a cloud model" })
    ).not.toBeInTheDocument();
  },
};
export const ReadOnly: Story = {
  args: {
    hosting: { allowed: false, reason: "Current credential cannot configure model hosting." },
    connection: { allowed: false, reason: "Current credential cannot register native models." },
  },
};
