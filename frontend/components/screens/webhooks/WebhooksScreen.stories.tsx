import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import type { AstroliftWebhookSubscription } from "@/graphql/operations/operations.types";

import { SubscriptionDetailView } from "./SubscriptionDetail";
import { WEBHOOKS_LIST } from "./webhooks-list";
import {
  LONG_SUBSCRIPTIONS,
  REVEAL,
  SUBSCRIPTION_DETAIL,
  SUBSCRIPTIONS,
  TEST_RESULT_FAILED,
  TEST_RESULT_OK,
  WEBHOOKS,
} from "./webhooks-zentinelle.fixtures";
import { WebhooksScreen, type WebhooksScreenProps } from "./WebhooksScreen";

const meta: Meta = {
  title: "Screens/Webhooks/WebhooksScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** The deliveries sheet renders its view with fixture deliveries. */
const renderDetail = (subscription: AstroliftWebhookSubscription) => (
  <SubscriptionDetailView {...SUBSCRIPTION_DETAIL} subscription={subscription} />
);

type Props = Partial<Omit<WebhooksScreenProps, "list" | "renderDetail">> & {
  initial?: Partial<ListState>;
};

function Screen({ initial, ...patch }: Props) {
  const list = useLocalListState(WEBHOOKS_LIST, initial);
  return <WebhooksScreen {...WEBHOOKS} {...patch} list={list} renderDetail={renderDetail} />;
}

export const Full: Story = { render: () => <Screen /> };

/** One subscription is changing; other subscriptions remain actionable. */
export const OneRowPending: Story = {
  render: () => <Screen pendingRows={new Set([SUBSCRIPTIONS[0].id])} />,
};

/** Scoped to one app (its Settings section): embedded, the description names it. */
export const AppScoped: Story = { render: () => <Screen appSlug="storefront" /> };

export const Loading: Story = { render: () => <Screen rows={[]} loading /> };

export const Empty: Story = { render: () => <Screen rows={[]} totalCount={0} /> };

/** A search that matches no subscription URL. */
export const EmptyFiltered: Story = {
  render: () => <Screen rows={[]} totalCount={0} initial={{ q: "pagerduty" }} />,
};

/** The page query failed: the list shows its retry state. */
export const LoadFailed: Story = {
  render: () => <Screen rows={[]} error={{ message: "Network error: failed to fetch" }} />,
};

/** Failing: narrowed on the page in hand, the note says so. */
export const Failing: Story = {
  render: () => (
    <Screen
      rows={SUBSCRIPTIONS.filter((s) => s.failureCount > 0)}
      totalCount={null}
      initial={{ view: "failing" }}
    />
  ),
};

/** Right after create or rotate: the one-time secret, plus a test result. */
export const SecretRevealed: Story = {
  render: () => <Screen reveal={REVEAL} testResult={TEST_RESULT_OK} />,
};

/** A test fire that never reached the subscriber. */
export const TestFailed: Story = { render: () => <Screen testResult={TEST_RESULT_FAILED} /> };

/** A row's `⋯` › Deliveries opens the delivery feed in a sheet. */
export const DeliveriesSheet: Story = {
  render: () => <Screen />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const [menu] = canvas.getAllByRole("button", { name: /row actions/i });
    if (menu) await userEvent.click(menu);
  },
};

export const LongStrings: Story = {
  render: () => (
    <Screen
      appSlug="a-very-long-application-slug-that-keeps-going-for-the-layout-test"
      reveal={{ ...REVEAL, subscription: LONG_SUBSCRIPTIONS[0] }}
      rows={LONG_SUBSCRIPTIONS}
      totalCount={LONG_SUBSCRIPTIONS.length}
      nextCursor="cursor-2"
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen
        rows={LONG_SUBSCRIPTIONS}
        reveal={{ ...REVEAL, subscription: LONG_SUBSCRIPTIONS[0] }}
      />
    </div>
  ),
};
