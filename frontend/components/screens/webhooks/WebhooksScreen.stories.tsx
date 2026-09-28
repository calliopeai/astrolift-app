import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftWebhookSubscription } from "@/graphql/operations/operations.types";

import { SubscriptionDetailView } from "./SubscriptionDetail";
import {
  LONG_SUBSCRIPTIONS,
  REVEAL,
  SUBSCRIPTION_DETAIL,
  TEST_RESULT_FAILED,
  TEST_RESULT_OK,
  WEBHOOKS,
} from "./webhooks-zentinelle.fixtures";
import { WebhooksScreen } from "./WebhooksScreen";

const meta: Meta = {
  title: "Screens/Webhooks/WebhooksScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** The expanded panel renders its view with fixture deliveries. */
const renderDetail = (subscription: AstroliftWebhookSubscription, onClose: () => void) => (
  <SubscriptionDetailView {...SUBSCRIPTION_DETAIL} subscription={subscription} onClose={onClose} />
);

export const Full: Story = {
  render: () => <WebhooksScreen {...WEBHOOKS} renderDetail={renderDetail} />,
};

/** Scoped to one app: the description names it and URLs are plain text. */
export const AppScoped: Story = {
  render: () => <WebhooksScreen {...WEBHOOKS} appSlug="storefront" renderDetail={renderDetail} />,
};

export const Loading: Story = {
  render: () => (
    <WebhooksScreen
      {...WEBHOOKS}
      table={fakeController<AstroliftWebhookSubscription>({ state: "loading" })}
      renderDetail={renderDetail}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <WebhooksScreen
      {...WEBHOOKS}
      table={fakeController<AstroliftWebhookSubscription>({ state: "empty", totalCount: 0 })}
      renderDetail={renderDetail}
    />
  ),
};

/** A search that matches no subscription URL. */
export const EmptyFiltered: Story = {
  render: () => (
    <WebhooksScreen
      {...WEBHOOKS}
      table={fakeController<AstroliftWebhookSubscription>({
        state: "emptyFiltered",
        search: "pagerduty",
        isFiltered: true,
        totalCount: 0,
      })}
      renderDetail={renderDetail}
    />
  ),
};

/** The page query failed: the table shows its retry state. */
export const LoadFailed: Story = {
  render: () => (
    <WebhooksScreen
      {...WEBHOOKS}
      table={fakeController<AstroliftWebhookSubscription>({
        state: "error",
        error: new Error("Network error: failed to fetch"),
      })}
      renderDetail={renderDetail}
    />
  ),
};

/** Right after create or rotate: the one-time secret, plus a test result. */
export const SecretRevealed: Story = {
  render: () => (
    <WebhooksScreen
      {...WEBHOOKS}
      reveal={REVEAL}
      testResult={TEST_RESULT_OK}
      renderDetail={renderDetail}
    />
  ),
};

/** A test fire that never reached the subscriber. */
export const TestFailed: Story = {
  render: () => (
    <WebhooksScreen {...WEBHOOKS} testResult={TEST_RESULT_FAILED} renderDetail={renderDetail} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <WebhooksScreen
      {...WEBHOOKS}
      appSlug="a-very-long-application-slug-that-keeps-going-for-the-layout-test"
      reveal={{ ...REVEAL, subscription: LONG_SUBSCRIPTIONS[0] }}
      table={fakeController<AstroliftWebhookSubscription>({
        rows: LONG_SUBSCRIPTIONS,
        totalCount: LONG_SUBSCRIPTIONS.length,
        sortEnabled: false,
      })}
      renderDetail={renderDetail}
    />
  ),
};
