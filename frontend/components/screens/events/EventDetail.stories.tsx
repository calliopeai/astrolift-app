import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EventDetail } from "./EventDetail";
import { EVENTS, EVENTS_LONG } from "./events-downloads.fixtures";

const meta: Meta = {
  title: "Screens/Events/EventDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <EventDetail id={EVENTS[0].id} event={EVENTS[0]} loading={false} />,
};

export const Loading: Story = {
  render: () => <EventDetail id={EVENTS[0].id} event={null} loading />,
};

/**
 * A successful direct read returned no visible event.
 */
export const NotFound: Story = {
  render: () => (
    <EventDetail id="9f0e1d2c-0000-4000-8000-000000000000" event={null} loading={false} />
  ),
};

/** No payload, and a source with no page of its own. */
export const Empty: Story = {
  render: () => <EventDetail id={EVENTS[2].id} event={{ ...EVENTS[3] }} loading={false} />,
};

export const LongStrings: Story = {
  render: () => <EventDetail id={EVENTS_LONG[0].id} event={EVENTS_LONG[0]} loading={false} />,
};

export const Error: Story = {
  render: () => (
    <EventDetail
      id={EVENTS[0].id}
      event={null}
      loading={false}
      error="Permission denied"
      onRetry={() => {}}
    />
  ),
};
