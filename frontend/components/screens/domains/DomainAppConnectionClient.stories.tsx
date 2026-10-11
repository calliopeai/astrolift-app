import type { Meta, StoryObj } from "@storybook/react";
import { DomainAppConnectionEntry } from "./DomainAppConnectionClient";
import { HTTP_API_CONNECTION } from "./DomainAppConnectionSheet.stories";
const meta = {
  title: "Domains/App connection entry",
  component: DomainAppConnectionEntry,
  args: { ...HTTP_API_CONNECTION },
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof DomainAppConnectionEntry>;
export default meta;
type Story = StoryObj<typeof meta>;
export const ReviewedHttpApi: Story = {};
export const RecordedConfiguration: Story = {
  args: {
    outcome: "recorded",
    operation: {
      id: "a0000000-0000-4000-8000-000000000005",
      purpose: "connect",
      status: "ready",
      dns: "configured",
      endpoint: "configured",
      tls: "unknown",
      reason: "CONFIGURATION_OBSERVED",
    },
    onRecover: () => {},
  },
};
export const UnknownReply: Story = {
  args: { outcome: "unknown", review: null, onRecover: () => {} },
};
