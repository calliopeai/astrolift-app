import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ManagedResourceCost } from "./ManagedResourceCost";
import { RESOURCE } from "./resource-reads.fixtures";

const meta: Meta<typeof ManagedResourceCost> = {
  title: "Screens/Projects/ManagedResourceCost",
  component: ManagedResourceCost,
  args: {
    preview: {
      managedServiceId: RESOURCE.id,
      available: true,
      reason: "",
      message: "",
      monthlyTotal: 42.5,
      currency: "USD",
      pricingSourceUrl: "https://prices.example.test/redis",
      pricingFetchedAt: "2026-10-02T00:00:00Z",
      notes: [],
      approximate: false,
    },
  },
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Evidence: Story = {};
export const Approximate: Story = {
  args: { preview: { ...meta.args!.preview!, approximate: true } },
};
export const ZeroWithEvidence: Story = {
  args: { preview: { ...meta.args!.preview!, monthlyTotal: 0 } },
};
export const MissingAmount: Story = {
  args: { preview: { ...meta.args!.preview!, monthlyTotal: null } },
};
export const InvalidSource: Story = {
  args: { preview: { ...meta.args!.preview!, pricingSourceUrl: "javascript:void(0)" } },
};
export const Unavailable: Story = {
  args: { preview: { ...meta.args!.preview!, available: false } },
};
