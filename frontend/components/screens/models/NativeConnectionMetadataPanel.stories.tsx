import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { NativeConnectionMetadataPanel } from "./NativeConnectionMetadataPanel";
import { projectedNativeModel } from "./native-model.fixtures";

const meta = {
  title: "Screens/Models/NativeConnectionMetadataPanel",
  component: NativeConnectionMetadataPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof NativeConnectionMetadataPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Bedrock: Story = { args: { model: projectedNativeModel("foundation") } };
export const VertexUnavailable: Story = {
  args: { model: projectedNativeModel("vertex_unadopted"), settings: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Google Vertex AI")).toBeVisible();
    await expect(canvas.queryByRole("button")).not.toBeInTheDocument();
  },
};
export const FoundryUnavailable: Story = {
  args: { model: projectedNativeModel("foundry_unadopted") },
};
export const UnknownUnavailable: Story = {
  args: { model: projectedNativeModel("unknown_family") },
};
