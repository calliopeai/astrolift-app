import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { DnsConnectionCallbackNotice } from "./DnsConnectionCallbackNotice";
const meta: Meta<typeof DnsConnectionCallbackNotice> = {
  title: "Screens/Domains/DnsConnectionCallbackNotice",
  component: DnsConnectionCallbackNotice,
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Saved: Story = { args: { outcome: "saved" } };
export const Unconfirmed: Story = { args: { outcome: "unconfirmed" } };
export const Denied: Story = { args: { outcome: "denied" } };
