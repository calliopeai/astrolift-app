import en from "@/messages/en.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { ModelConnectionPolicyPanel } from "./ModelConnectionPolicyPanel";
import { connectionPolicyProps } from "./model-connection.fixtures";
const meta = {
  title: "Screens/Models/ModelConnectionPolicy",
  component: ModelConnectionPolicyPanel,
  args: connectionPolicyProps,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelConnectionPolicyPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Organization: Story = {
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: en.models.shared.connections.savePolicy }));
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent(en.models.shared.connections.policyReviewNotice);
  },
};
export const Restriction: Story = {
  args: {
    restriction: true,
    policy: { id: null, version: 0, mode: "AUTO", requiredApprovals: 1, allowSelfApproval: true },
  },
};
export const Unknown: Story = { args: { policy: null, loading: true } };
export const CachedFailedRead: Story = {
  args: { error: "Current policy read unavailable", stale: true },
};
export const ReadOnly: Story = { args: { canEdit: false } };
export const SavedReadFailed: Story = {
  args: {
    onAccepted: async () => {
      throw new Error("read unavailable");
    },
  },
};
