import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EVENTS_LONG, eventsFeed, FEED_ERROR } from "./events-downloads.fixtures";
import { RawEventsList } from "./RawEventsList";

const meta: Meta = { title: "Screens/Events/RawEventsList" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <RawEventsList events={eventsFeed()} /> };

export const Loading: Story = {
  render: () => <RawEventsList events={eventsFeed({ items: [], loading: true })} />,
};

export const Empty: Story = { render: () => <RawEventsList events={eventsFeed({ items: [] })} /> };

export const ErrorState: Story = {
  render: () => <RawEventsList events={eventsFeed({ items: [], error: FEED_ERROR })} />,
};

/** Older events behind the cursor: Load older at the end of the frame. */
export const HasOlder: Story = {
  render: () => <RawEventsList events={eventsFeed({ hasMore: true })} />,
};

/** An older page failed: the events stay, the error sits at the end. */
export const OlderPageFailed: Story = {
  render: () => <RawEventsList events={eventsFeed({ hasMore: true, error: FEED_ERROR })} />,
};

/** New events arrived above the ones being read. */
export const NewEvents: Story = {
  render: () => <RawEventsList events={eventsFeed({ newCount: 4 })} />,
};

export const LongStrings: Story = {
  render: () => <RawEventsList events={eventsFeed({ items: EVENTS_LONG })} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <RawEventsList events={eventsFeed({ items: EVENTS_LONG })} />
    </div>
  ),
};
