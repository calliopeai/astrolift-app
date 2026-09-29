import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DeliveryDetailScreen } from "./DeliveryDetailScreen";
import { DELIVERIES, DELIVERY_DETAIL, LONG_DELIVERY } from "./webhooks-zentinelle.fixtures";

const meta: Meta = {
  title: "Screens/Webhooks/DeliveryDetailScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** A transport failure: error card, request payload, no response body. */
export const Full: Story = {
  render: () => <DeliveryDetailScreen {...DELIVERY_DETAIL} />,
};

/** A successful delivery: no error card. */
export const Delivered: Story = {
  render: () => (
    <DeliveryDetailScreen
      {...DELIVERY_DETAIL}
      deliveryId={DELIVERIES[0].id}
      delivery={DELIVERIES[0]}
    />
  ),
};

export const Loading: Story = {
  render: () => <DeliveryDetailScreen {...DELIVERY_DETAIL} loading delivery={null} />,
};

/** Nothing recorded for the request or the response. */
export const Empty: Story = {
  render: () => (
    <DeliveryDetailScreen
      {...DELIVERY_DETAIL}
      delivery={{ ...DELIVERIES[3], requestPayloadExcerpt: "", responseBodyExcerpt: "" }}
    />
  ),
};

/**
 * The screen has no error state of its own; the closest real one is
 * not-found (a delivery outside its subscription's 50-row window).
 */
export const NotFound: Story = {
  render: () => <DeliveryDetailScreen {...DELIVERY_DETAIL} delivery={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <DeliveryDetailScreen
      {...DELIVERY_DETAIL}
      deliveryId={LONG_DELIVERY.id}
      delivery={LONG_DELIVERY}
    />
  ),
};
