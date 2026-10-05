import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { default as panelMeta, DELIVERY_PANEL, DELIVERY_TEST } from "./EmailDeliveryPanel.stories";
import { EmailDeliveryPanel } from "./EmailDeliveryPanel";

// Present the connected screen's states with the same pure panel it renders.
// Live identity, source review and replay behavior have separate HTTP tests.
const meta = {
  ...panelMeta,
  title: "Screens/EmailDelivery/EmailDeliveryClient",
  component: EmailDeliveryPanel,
  args: DELIVERY_PANEL,
} satisfies Meta<typeof EmailDeliveryPanel>;
export default meta;
type Story = StoryObj<typeof meta>;

export const NeedsReview: Story = {
  args: {
    draft: { recipient: "success@simulator.amazonses.com", subject: "", body: "" },
    canReview: true,
  },
};
export const Accepted: Story = {
  args: {
    locked: true,
    requestId: DELIVERY_TEST.requestId,
    current: DELIVERY_TEST,
    canStartAnother: true,
  },
};
export const Uncertain: Story = {
  args: { locked: true, requestId: DELIVERY_TEST.requestId, message: "uncertain" },
};
