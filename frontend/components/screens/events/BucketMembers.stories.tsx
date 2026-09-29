import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { BucketMembers } from "./BucketMembers";
import { BUCKET_MEMBERS, EVENTS_LONG } from "./events-downloads.fixtures";

const meta: Meta = { title: "Screens/Events/BucketMembers" };
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <BucketMembers loading={false} members={BUCKET_MEMBERS} />,
};

export const Loading: Story = { render: () => <BucketMembers loading members={[]} /> };

/** The members aged out of the window the fetch reads. There is no error state. */
export const Empty: Story = { render: () => <BucketMembers loading={false} members={[]} /> };

export const LongStrings: Story = {
  render: () => <BucketMembers loading={false} members={EVENTS_LONG} />,
};
