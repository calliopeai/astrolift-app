import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { NativeModelObservationsPanel } from "./NativeModelObservationsPanel";

const meta = {
  title: "Screens/Models/NativeModelObservationsPanel",
  component: NativeModelObservationsPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof NativeModelObservationsPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Unsupported: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Inference access has not been verified.")).toBeVisible();
    await expect(canvas.queryByRole("button")).not.toBeInTheDocument();
  },
};
