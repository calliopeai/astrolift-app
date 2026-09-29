import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DeliveriesFeed } from "./DeliveriesFeed";
import { DELIVERIES, DELIVERIES_FEED, LONG_DELIVERY } from "./webhooks-zentinelle.fixtures";

const meta: Meta = {
  title: "Screens/Webhooks/DeliveriesFeed",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const HINT = "Use the Send test event action on the webhooks list to fire one.";

export const Full: Story = {
  render: () => (
    <DeliveriesFeed deliveries={DELIVERIES_FEED} emptyHint={HINT} hrefOf={(d) => `#${d.id}`} />
  ),
};

/** More behind the cursor: Load older at the end of the frame. */
export const HasOlder: Story = {
  render: () => (
    <DeliveriesFeed deliveries={{ ...DELIVERIES_FEED, hasMore: true }} emptyHint={HINT} />
  ),
};

export const Loading: Story = {
  render: () => (
    <DeliveriesFeed
      deliveries={{ ...DELIVERIES_FEED, items: [], loading: true }}
      emptyHint={HINT}
    />
  ),
};

export const Empty: Story = {
  render: () => <DeliveriesFeed deliveries={{ ...DELIVERIES_FEED, items: [] }} emptyHint={HINT} />,
};

export const ErrorState: Story = {
  render: () => (
    <DeliveriesFeed
      deliveries={{ ...DELIVERIES_FEED, items: [], error: "Network error: failed to fetch" }}
      emptyHint={HINT}
    />
  ),
};

/** An older page failed: the rows stay, the error sits at the end with Load older. */
export const OlderPageFailed: Story = {
  render: () => (
    <DeliveriesFeed
      deliveries={{ ...DELIVERIES_FEED, hasMore: true, error: "upstream timed out" }}
      emptyHint={HINT}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <DeliveriesFeed
      deliveries={{ ...DELIVERIES_FEED, items: [LONG_DELIVERY, ...DELIVERIES] }}
      emptyHint={HINT}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <DeliveriesFeed
        deliveries={{ ...DELIVERIES_FEED, items: [LONG_DELIVERY, ...DELIVERIES] }}
        emptyHint={HINT}
      />
    </div>
  ),
};
