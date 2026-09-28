import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { AggregatedEventsList } from "./AggregatedEventsList";
import { BucketMembers } from "./BucketMembers";
import { EventsAggregateToggle } from "./EventsScreen";
import {
  BUCKET_MEMBERS,
  BUCKETS_LONG,
  bucketsTable,
  EVENTS_LONG,
  TABLE_ERROR,
} from "./events-downloads.fixtures";

const meta: Meta = { title: "Screens/Events/AggregatedEventsList" };
export default meta;

type Story = StoryObj;

const toolbar = <EventsAggregateToggle checked onCheckedChange={() => {}} />;
const members = () => <BucketMembers loading={false} members={BUCKET_MEMBERS} />;

export const Full: Story = {
  render: () => (
    <AggregatedEventsList table={bucketsTable()} toolbar={toolbar} renderMembers={members} />
  ),
};

export const Loading: Story = {
  render: () => (
    <AggregatedEventsList
      table={bucketsTable({ state: "loading", rows: [], totalCount: null })}
      toolbar={toolbar}
      renderMembers={members}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <AggregatedEventsList
      table={bucketsTable({ state: "empty", rows: [], totalCount: 0 })}
      toolbar={toolbar}
      renderMembers={members}
    />
  ),
};

export const EmptyFiltered: Story = {
  render: () => (
    <AggregatedEventsList
      table={bucketsTable({
        state: "emptyFiltered",
        rows: [],
        totalCount: 0,
        isFiltered: true,
        search: "nope",
      })}
      toolbar={toolbar}
      renderMembers={members}
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <AggregatedEventsList
      table={bucketsTable({ state: "error", rows: [], error: TABLE_ERROR })}
      toolbar={toolbar}
      renderMembers={members}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AggregatedEventsList
      table={bucketsTable({ rows: BUCKETS_LONG, totalCount: 1 })}
      toolbar={toolbar}
      renderMembers={() => <BucketMembers loading={false} members={EVENTS_LONG} />}
    />
  ),
};

/** Expand opens the bucket's members in a dialog. */
export const MembersOpen: Story = {
  render: () => (
    <AggregatedEventsList table={bucketsTable()} toolbar={toolbar} renderMembers={members} />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getAllByRole("button", { name: "Expand" })[0]);
    const dialog = within(await within(document.body).findByRole("dialog"));
    await expect(dialog.getAllByRole("link", { name: /app\.deployed/ }).length).toBeGreaterThan(0);
  },
};
