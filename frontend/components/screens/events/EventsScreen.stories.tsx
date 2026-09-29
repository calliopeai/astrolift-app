import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type * as React from "react";

import { AggregatedEventsList } from "./AggregatedEventsList";
import { BucketMembers } from "./BucketMembers";
import { EventRate } from "./EventRate";
import { EventsScreen } from "./EventsScreen";
import {
  BUCKET_MEMBERS,
  BUCKETS_LONG,
  bucketsFeed,
  eventsFeed,
  FEED_ERROR,
  RATE_DAYS,
  RATE_TOTAL,
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
const members = () => <BucketMembers loading={false} members={BUCKET_MEMBERS} />;

function Screen({
  children,
  aggregate = true,
  search = "",
  withRate = rate,
}: {
  children: React.ReactNode;
  aggregate?: boolean;
  search?: string;
  withRate?: React.ReactNode;
}) {
  return (
    <EventsScreen
      rate={withRate}
      search={search}
      onSearchChange={() => {}}
      aggregate={aggregate}
      onAggregateChange={() => {}}
    >
      {children}
    </EventsScreen>
  );
}

/** The default view: grouped. */
export const Full: Story = {
  render: () => (
    <Screen>
      <AggregatedEventsList buckets={bucketsFeed()} renderMembers={members} />
    </Screen>
  ),
};

export const Ungrouped: Story = {
  render: () => (
    <Screen aggregate={false}>
      <RawEventsList events={eventsFeed()} />
    </Screen>
  ),
};

export const Loading: Story = {
  render: () => (
    <Screen withRate={noRate}>
      <AggregatedEventsList
        buckets={bucketsFeed({ items: [], loading: true })}
        renderMembers={members}
      />
    </Screen>
  ),
};

export const Empty: Story = {
  render: () => (
    <Screen withRate={noRate}>
      <AggregatedEventsList buckets={bucketsFeed({ items: [] })} renderMembers={members} />
    </Screen>
  ),
};

/** A search typed into the box; the feed answers the settled term. */
export const Searching: Story = {
  render: () => (
    <Screen search="workload.unhealthy" aggregate={false}>
      <RawEventsList events={eventsFeed({ items: [] })} />
    </Screen>
  ),
};

export const ErrorState: Story = {
  render: () => (
    <Screen>
      <AggregatedEventsList
        buckets={bucketsFeed({ items: [], error: FEED_ERROR })}
        renderMembers={members}
      />
    </Screen>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Screen search={"x".repeat(200)}>
      <AggregatedEventsList
        buckets={bucketsFeed({ items: BUCKETS_LONG })}
        renderMembers={members}
      />
    </Screen>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen>
        <AggregatedEventsList
          buckets={bucketsFeed({ items: BUCKETS_LONG })}
          renderMembers={members}
        />
      </Screen>
    </div>
  ),
};
