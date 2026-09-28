"use client";

import * as React from "react";

import { AggregatedEventsList } from "@/components/screens/events/AggregatedEventsList";
import { BucketMembers } from "@/components/screens/events/BucketMembers";
import { EventRate } from "@/components/screens/events/EventRate";
import { EventsAggregateToggle, EventsScreen } from "@/components/screens/events/EventsScreen";
import { RawEventsList } from "@/components/screens/events/RawEventsList";
import {
  type AggregatedEvent,
  useAggregatedEvents,
  useBucketMembers,
  useEventRate,
  useRawEvents,
} from "@/components/screens/events/use-events";

export function EventsClient() {
  const [aggregate, setAggregate] = React.useState(true);
  const toolbar = <EventsAggregateToggle checked={aggregate} onCheckedChange={setAggregate} />;

  return (
    <EventsScreen rate={<EventRateContainer />}>
      {aggregate ? (
        <AggregatedListContainer toolbar={toolbar} />
      ) : (
        <RawListContainer toolbar={toolbar} />
      )}
    </EventsScreen>
  );
}

function EventRateContainer() {
  return <EventRate {...useEventRate()} />;
}

function RawListContainer({ toolbar }: { toolbar: React.ReactNode }) {
  return <RawEventsList {...useRawEvents()} toolbar={toolbar} />;
}

function AggregatedListContainer({ toolbar }: { toolbar: React.ReactNode }) {
  return (
    <AggregatedEventsList
      {...useAggregatedEvents()}
      toolbar={toolbar}
      renderMembers={(bucket) => <BucketMembersContainer bucket={bucket} />}
    />
  );
}

function BucketMembersContainer({ bucket }: { bucket: AggregatedEvent }) {
  return <BucketMembers {...useBucketMembers(bucket)} />;
}
