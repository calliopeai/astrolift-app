import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { ModelHostingSourceChoice } from "./ModelHostingSourceChoice";

const meta = {
  title: "Screens/Models/ModelHostingSourceChoice",
  component: ModelHostingSourceChoice,
  parameters: { layout: "padded" },
  args: { value: "huggingface", allowed: true, onChange: () => {} },
} satisfies Meta<typeof ModelHostingSourceChoice>;
export default meta;
type Story = StoryObj<typeof meta>;
export const HuggingFace: Story = {};
export const LocalFiles: Story = { args: { value: "local" } };
export const ReadOnly: Story = {
  args: { allowed: false },
  play: async ({ canvasElement }) => {
    for (const button of within(canvasElement).getAllByRole("button"))
      await expect(button).toBeDisabled();
  },
};
export const CheckingPermission: Story = { args: { allowed: null } };
