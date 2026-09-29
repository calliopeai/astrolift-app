"use client";

import * as React from "react";

import { AggregatedEventsList } from "@/components/screens/events/AggregatedEventsList";
import { BucketMembers } from "@/components/screens/events/BucketMembers";
import { EventRate } from "@/components/screens/events/EventRate";
import { EventsScreen } from "@/components/screens/events/EventsScreen";
import { RawEventsList } from "@/components/screens/events/RawEventsList";
import {
  type AggregatedEvent,
  useAggregatedEvents,
  useBucketMembers,
  useEventRate,
  useRawEvents,
} from "@/components/screens/events/use-events";
import { useDebounce } from "@/hooks/use-debounce";

export function EventsClient() {
  const [aggregate, setAggregate] = React.useState(true);
  const [search, setSearch] = React.useState("");
  // Only the settled term reaches the server, never each keystroke.
  const settled = useDebounce(search, 300);

  return (
    <EventsScreen
      rate={<EventRateContainer />}
      search={search}
      onSearchChange={setSearch}
      aggregate={aggregate}
      onAggregateChange={setAggregate}
    >
      {aggregate ? (
        <AggregatedFeedContainer search={settled} />
      ) : (
        <RawFeedContainer search={settled} />
      )}
    </EventsScreen>
  );
}

function EventRateContainer() {
  return <EventRate {...useEventRate()} />;
}

function RawFeedContainer({ search }: { search: string }) {
  return <RawEventsList {...useRawEvents(search)} />;
}

function AggregatedFeedContainer({ search }: { search: string }) {
  return (
    <AggregatedEventsList
      {...useAggregatedEvents(search)}
      renderMembers={(bucket) => <BucketMembersContainer bucket={bucket} />}
    />
  );
}

function BucketMembersContainer({ bucket }: { bucket: AggregatedEvent }) {
  return <BucketMembers {...useBucketMembers(bucket)} />;
}
