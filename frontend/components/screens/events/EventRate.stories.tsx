import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EventRate } from "./EventRate";
import { RATE_DAYS, RATE_TOTAL } from "./events-downloads.fixtures";

const meta: Meta = { title: "Screens/Events/EventRate" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <EventRate days={RATE_DAYS} total={RATE_TOTAL} /> };

/**
 * The card has no loading or error state of its own: until events arrive
 * (or when none fall in the window) it renders nothing, shown here beside
 * a marker so the story has a canvas.
 */
export const Empty: Story = {
  render: () => (
    <>
      <EventRate days={new Array<number>(14).fill(0)} total={0} />
      <EventRate days={[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]} total={1} />
    </>
  ),
};

export const LargeCount: Story = {
  render: () => <EventRate days={RATE_DAYS.map((d) => d * 10_000)} total={RATE_TOTAL * 10_000} />,
};
