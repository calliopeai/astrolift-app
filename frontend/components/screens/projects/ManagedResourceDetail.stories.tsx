import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { ManagedResourceDetail } from "./ManagedResourceDetail";
import { RESOURCE, RESOURCE_DETAIL, ATTACHMENT } from "./resource-reads.fixtures";

const meta: Meta<typeof ManagedResourceDetail> = {
  title: "Screens/Projects/ManagedResourceDetail",
  component: ManagedResourceDetail,
  args: RESOURCE_DETAIL,
};
export default meta;
type Story = StoryObj<typeof meta>;
export const PagedConsumers: Story = {};
export const ReadOnly: Story = { args: { canUpdate: false, canPreview: false } };
export const Refused: Story = { args: { current: null, refused: true } };
export const ChangedContext: Story = {
  args: { current: { ...RESOURCE, contextRevision: "changed-cluster" } },
};
export const Loading: Story = { args: { current: null, loading: true } };
export const ConsumerReadFailure: Story = {
  args: {
    attachments: fakeController({
      rows: [],
      state: "error",
      error: new Error("Consumer read unavailable"),
    }),
  },
};
export const PriceEvidence: Story = {
  args: {
    cost: {
      managedServiceId: RESOURCE.id,
      available: true,
      reason: "",
      message: "",
      monthlyTotal: 42.5,
      currency: "USD",
      pricingSourceUrl: "https://prices.example.test/redis",
      pricingFetchedAt: "2026-10-02T00:00:00Z",
      notes: ["List price excludes negotiated discounts."],
      approximate: true,
    },
  },
};
export const MissingPrice: Story = {
  args: {
    cost: {
      managedServiceId: RESOURCE.id,
      available: true,
      reason: "",
      message: "",
      monthlyTotal: null,
      currency: "USD",
      pricingSourceUrl: "https://prices.example.test/redis",
      pricingFetchedAt: "2026-10-02T00:00:00Z",
      notes: [],
      approximate: false,
    },
  },
};

const longResource = {
  ...RESOURCE,
  name: "shared-resource-".repeat(24),
  clusterSlug: "cluster-".repeat(24),
  operationWorkflowId: "operation-".repeat(24),
};
export const LongStrings: Story = {
  args: {
    target: longResource,
    current: longResource,
    attachments: fakeController({
      rows: [{ ...ATTACHMENT, consumerSlug: "consumer-".repeat(24) }],
      totalCount: 251,
      hasNext: true,
    }),
  },
};
export const Width768: Story = { parameters: { viewport: { defaultViewport: "tablet" } } };
