import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { AggregatedEventsList } from "./AggregatedEventsList";
import { BucketMembers } from "./BucketMembers";
import {
  BUCKET_MEMBERS,
  BUCKETS_LONG,
  bucketsFeed,
  EVENTS_LONG,
  FEED_ERROR,
} from "./events-downloads.fixtures";

const meta: Meta = { title: "Screens/Events/AggregatedEventsList" };
export default meta;

type Story = StoryObj;

const members = () => <BucketMembers loading={false} members={BUCKET_MEMBERS} />;

export const Full: Story = {
  render: () => <AggregatedEventsList buckets={bucketsFeed()} renderMembers={members} />,
};

export const Loading: Story = {
  render: () => (
    <AggregatedEventsList
      buckets={bucketsFeed({ items: [], loading: true })}
      renderMembers={members}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <AggregatedEventsList buckets={bucketsFeed({ items: [] })} renderMembers={members} />
  ),
};

/** Older buckets behind the cursor: Load older at the end of the frame. */
export const HasOlder: Story = {
  render: () => (
    <AggregatedEventsList buckets={bucketsFeed({ hasMore: true })} renderMembers={members} />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <AggregatedEventsList
      buckets={bucketsFeed({ items: [], error: FEED_ERROR })}
      renderMembers={members}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AggregatedEventsList
      buckets={bucketsFeed({ items: BUCKETS_LONG })}
      renderMembers={() => <BucketMembers loading={false} members={EVENTS_LONG} />}
    />
  ),
};

/** Expand opens the bucket's members in a dialog. */
export const MembersOpen: Story = {
  render: () => <AggregatedEventsList buckets={bucketsFeed()} renderMembers={members} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getAllByRole("button", { name: "Expand" })[0]);
    const dialog = within(await within(document.body).findByRole("dialog"));
    await expect(dialog.getAllByRole("link", { name: /app\.deployed/ }).length).toBeGreaterThan(0);
  },
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <AggregatedEventsList
        buckets={bucketsFeed({ items: BUCKETS_LONG })}
        renderMembers={members}
      />
    </div>
  ),
};
