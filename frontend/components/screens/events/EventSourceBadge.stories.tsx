import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EVENTS, EVENTS_LONG } from "./events-downloads.fixtures";
import { PayloadPreview, SourceBadge } from "./EventSourceBadge";

const meta: Meta = { title: "Screens/Events/EventSourceBadge" };
export default meta;

type Story = StoryObj;

/** A resource the platform can link to: rendered as a link badge. */
export const Linked: Story = {
  render: () => (
    <SourceBadge resourceKind="app" resourceId="checkout-api" payload={EVENTS[0].payload} />
  ),
};

/** A resource kind with no page: text-only badge. */
export const Unlinked: Story = {
  render: () => <SourceBadge resourceKind="webhook" resourceId="wh_19" payload={{}} />,
};

/** No source at all renders nothing; the payload preview shows its dash. */
export const Empty: Story = {
  render: () => (
    <>
      <SourceBadge resourceKind="" resourceId="" payload={{}} />
      <PayloadPreview payload={{}} />
    </>
  ),
};

export const Payload: Story = { render: () => <PayloadPreview payload={EVENTS[1].payload} /> };

export const LongStrings: Story = {
  render: () => (
    <>
      <SourceBadge
        resourceKind={EVENTS_LONG[0].resourceKind}
        resourceId={EVENTS_LONG[0].resourceId}
        payload={EVENTS_LONG[0].payload}
      />
      <PayloadPreview payload={EVENTS_LONG[0].payload} />
    </>
  ),
};
