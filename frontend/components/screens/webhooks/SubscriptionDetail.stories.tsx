import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftWebhookDelivery } from "@/graphql/operations/operations.types";

import { SubscriptionDetailView } from "./SubscriptionDetail";
import {
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
      deliveries={fakeController<AstroliftWebhookDelivery>({ state: "loading", pageSize: 10 })}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <SubscriptionDetailView
      {...SUBSCRIPTION_DETAIL}
      deliveries={fakeController<AstroliftWebhookDelivery>({
        state: "empty",
        totalCount: 0,
        pageSize: 10,
      })}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <SubscriptionDetailView
      {...SUBSCRIPTION_DETAIL}
      deliveries={fakeController<AstroliftWebhookDelivery>({
        state: "error",
        error: new Error("Network error: failed to fetch"),
        pageSize: 10,
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <SubscriptionDetailView
      {...SUBSCRIPTION_DETAIL}
      subscription={LONG_SUBSCRIPTIONS[0]}
      deliveries={fakeController<AstroliftWebhookDelivery>({
        rows: [LONG_DELIVERY],
        totalCount: 1,
        pageSize: 10,
        sortEnabled: false,
      })}
    />
  ),
};
