import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EventsAggregateToggle } from "./EventsScreen";
import { EVENTS_LONG, eventsTable, TABLE_ERROR } from "./events-downloads.fixtures";
import { RawEventsList } from "./RawEventsList";

const meta: Meta = { title: "Screens/Events/RawEventsList" };
export default meta;

type Story = StoryObj;

const toolbar = <EventsAggregateToggle checked={false} onCheckedChange={() => {}} />;

export const Full: Story = {
  render: () => <RawEventsList table={eventsTable()} toolbar={toolbar} />,
};

export const Loading: Story = {
  render: () => (
    <RawEventsList table={eventsTable({ state: "loading", rows: [] })} toolbar={toolbar} />
  ),
};

export const Empty: Story = {
  render: () => (
    <RawEventsList table={eventsTable({ state: "empty", rows: [] })} toolbar={toolbar} />
  ),
};

export const EmptyFiltered: Story = {
  render: () => (
    <RawEventsList
      table={eventsTable({ state: "emptyFiltered", rows: [], isFiltered: true, search: "nope" })}
      toolbar={toolbar}
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <RawEventsList
      table={eventsTable({ state: "error", rows: [], error: TABLE_ERROR })}
      toolbar={toolbar}
    />
  ),
};

export const NextPage: Story = {
  render: () => <RawEventsList table={eventsTable({ hasNext: true })} toolbar={toolbar} />,
};

export const LongStrings: Story = {
  render: () => <RawEventsList table={eventsTable({ rows: EVENTS_LONG })} toolbar={toolbar} />,
};
