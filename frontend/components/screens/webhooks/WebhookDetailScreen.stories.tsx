import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftWebhookDelivery } from "@/graphql/operations/operations.types";

import { LONG_DELIVERY, LONG_SUBSCRIPTIONS, WEBHOOK_DETAIL } from "./webhooks-zentinelle.fixtures";
import { WebhookDetailScreen } from "./WebhookDetailScreen";

const meta: Meta = {
  title: "Screens/Webhooks/WebhookDetailScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <WebhookDetailScreen {...WEBHOOK_DETAIL} />,
};

export const Loading: Story = {
  render: () => <WebhookDetailScreen {...WEBHOOK_DETAIL} loading subscription={null} />,
};

/** The subscription resolved but has never delivered. */
export const Empty: Story = {
  render: () => (
    <WebhookDetailScreen
      {...WEBHOOK_DETAIL}
      deliveries={fakeController<AstroliftWebhookDelivery>({ state: "empty", totalCount: 0 })}
    />
  ),
};

/**
 * The screen has no error state of its own (the shell shows not-found
 * when the subscription does not resolve), so this is the closest real
 * one: an id outside the LIST_WEBHOOKS window.
 */
export const NotFound: Story = {
  render: () => <WebhookDetailScreen {...WEBHOOK_DETAIL} subscription={null} />,
};

/** The deliveries page query failed: the table shows its retry state. */
export const DeliveriesFailed: Story = {
  render: () => (
    <WebhookDetailScreen
      {...WEBHOOK_DETAIL}
      deliveries={fakeController<AstroliftWebhookDelivery>({
        state: "error",
        error: new Error("Network error: failed to fetch"),
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <WebhookDetailScreen
      {...WEBHOOK_DETAIL}
      id={LONG_SUBSCRIPTIONS[0].id}
      subscription={LONG_SUBSCRIPTIONS[0]}
      deliveries={fakeController<AstroliftWebhookDelivery>({
        rows: [LONG_DELIVERY],
        totalCount: 1,
        sortEnabled: false,
      })}
    />
  ),
};
