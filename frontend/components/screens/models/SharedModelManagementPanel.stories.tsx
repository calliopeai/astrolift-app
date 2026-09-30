import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { SharedModelManagementPanel } from "./SharedModelManagementPanel";
import { sharedModelManagementProps } from "./shared-model-management.fixtures";
const meta = {
  title: "Screens/Models/SharedModelManagementPanel",
  component: SharedModelManagementPanel,
  parameters: { layout: "padded" },
  args: sharedModelManagementProps,
} satisfies Meta<typeof SharedModelManagementPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Owner: Story = {};
export const Reader: Story = { args: { canManage: false } };
export const Busy: Story = {
  args: { model: { ...sharedModelManagementProps.model, status: "updating" } },
};
export const UnknownRuntimeCleanup: Story = {
  args: {
    admission: null,
    model: { ...sharedModelManagementProps.model, status: "failed", runtimeSupported: null },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Review resource update" })).toBeDisabled();
    await expect(canvas.getByRole("button", { name: "Review deprovisioning" })).toBeEnabled();
  },
};
export const UpdateReview: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Review resource update" })
    );
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent("temporarily lose access");
  },
};
export const DeleteReview: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Review deprovisioning" })
    );
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent("retained");
  },
};
