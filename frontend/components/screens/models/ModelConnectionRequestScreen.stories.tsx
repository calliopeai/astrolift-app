import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { ModelConnectionRequestScreen } from "./ModelConnectionRequestScreen";
import { connectionRequestProps } from "./model-connection.fixtures";
const meta = {
  title: "Screens/Models/ModelConnectionRequest",
  component: ModelConnectionRequestScreen,
  args: connectionRequestProps,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelConnectionRequestScreen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Pending: Story = {};
export const Reviewer: Story = {
  args: {
    row: { ...connectionRequestProps.row!, canApprove: true, canReject: true, canCancel: false },
  },
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Approve" }));
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent("does not connect");
  },
};
export const Approved: Story = {
  args: {
    row: {
      ...connectionRequestProps.row!,
      status: "APPROVED",
      canFinalize: true,
      approvalCount: 2,
    },
  },
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await expect(c.getByText(/current requester must still choose Connect/)).toBeVisible();
    await userEvent.click(c.getByRole("button", { name: "Connect" }));
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent("current");
  },
};
export const Recorded: Story = {
  args: {
    row: {
      ...connectionRequestProps.row!,
      status: "APPROVED",
      subscriptionId: "subscription-one",
      canFinalize: false,
      canCancel: false,
    },
  },
};
export const Withdrawn: Story = {
  args: { ...connectionRequestProps, stale: true, error: "Current request access unavailable" },
};
export const SavedReadFailed: Story = {
  args: {
    onAccepted: async () => {
      throw new Error("read unavailable");
    },
  },
};
