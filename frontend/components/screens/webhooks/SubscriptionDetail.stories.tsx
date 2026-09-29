import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SubscriptionDetailView } from "./SubscriptionDetail";
import {
  DELIVERIES_FEED,
  LONG_DELIVERY,
  LONG_SUBSCRIPTIONS,
  SUBSCRIPTION_DETAIL,
} from "./webhooks-zentinelle.fixtures";

const meta: Meta = {
  title: "Screens/Webhooks/SubscriptionDetail",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <SubscriptionDetailView {...SUBSCRIPTION_DETAIL} />,
};

export const Loading: Story = {
  render: () => (
    <SubscriptionDetailView
      {...SUBSCRIPTION_DETAIL}
      deliveries={{ ...DELIVERIES_FEED, items: [], loading: true }}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <SubscriptionDetailView
      {...SUBSCRIPTION_DETAIL}
      deliveries={{ ...DELIVERIES_FEED, items: [] }}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <SubscriptionDetailView
      {...SUBSCRIPTION_DETAIL}
      deliveries={{ ...DELIVERIES_FEED, items: [], error: "Network error: failed to fetch" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <SubscriptionDetailView
      subscription={LONG_SUBSCRIPTIONS[0]}
      deliveries={{ ...DELIVERIES_FEED, items: [LONG_DELIVERY], hasMore: true }}
    />
  ),
};
