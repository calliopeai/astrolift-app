import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AggregatedEventsList } from "./AggregatedEventsList";
import { BucketMembers } from "./BucketMembers";
import { EventRate } from "./EventRate";
import { EventsAggregateToggle, EventsScreen } from "./EventsScreen";
import {
  BUCKET_MEMBERS,
  BUCKETS_LONG,
  bucketsTable,
  eventsTable,
  RATE_DAYS,
  RATE_TOTAL,
  TABLE_ERROR,
} from "./events-downloads.fixtures";
import { RawEventsList } from "./RawEventsList";

const meta: Meta = {
  title: "Screens/Events/EventsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const rate = <EventRate days={RATE_DAYS} total={RATE_TOTAL} />;
const noRate = <EventRate days={new Array<number>(14).fill(0)} total={0} />;
const grouped = <EventsAggregateToggle checked onCheckedChange={() => {}} />;
const members = () => <BucketMembers loading={false} members={BUCKET_MEMBERS} />;

/** The default view: grouped. */
export const Full: Story = {
  render: () => (
    <EventsScreen rate={rate}>
      <AggregatedEventsList table={bucketsTable()} toolbar={grouped} renderMembers={members} />
    </EventsScreen>
  ),
};

export const Ungrouped: Story = {
  render: () => (
    <EventsScreen rate={rate}>
      <RawEventsList
        table={eventsTable()}
        toolbar={<EventsAggregateToggle checked={false} onCheckedChange={() => {}} />}
      />
    </EventsScreen>
  ),
};

export const Loading: Story = {
  render: () => (
    <EventsScreen rate={noRate}>
      <AggregatedEventsList
        table={bucketsTable({ state: "loading", rows: [], totalCount: null })}
        toolbar={grouped}
        renderMembers={members}
      />
    </EventsScreen>
  ),
};

export const Empty: Story = {
  render: () => (
    <EventsScreen rate={noRate}>
      <AggregatedEventsList
        table={bucketsTable({ state: "empty", rows: [], totalCount: 0 })}
        toolbar={grouped}
        renderMembers={members}
      />
    </EventsScreen>
  ),
};

export const ErrorState: Story = {
  render: () => (
    <EventsScreen rate={rate}>
      <AggregatedEventsList
        table={bucketsTable({ state: "error", rows: [], error: TABLE_ERROR })}
        toolbar={grouped}
        renderMembers={members}
      />
    </EventsScreen>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <EventsScreen rate={rate}>
      <AggregatedEventsList
        table={bucketsTable({ rows: BUCKETS_LONG, totalCount: 1 })}
        toolbar={grouped}
        renderMembers={members}
      />
    </EventsScreen>
  ),
};
