import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EventRate } from "./EventRate";
import { RATE_DAYS, RATE_TOTAL } from "./events-downloads.fixtures";

const meta: Meta = { title: "Screens/Events/EventRate" };
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => (
    <EventRate
      loading={false}
      error={null}
      onRetry={() => {}}
      days={RATE_DAYS}
      total={RATE_TOTAL}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <>
      <EventRate
        loading={false}
        error={null}
        onRetry={() => {}}
        days={new Array<number>(14).fill(0)}
        total={0}
      />
      <EventRate
        loading={false}
        error={null}
        onRetry={() => {}}
        days={[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]}
        total={1}
      />
    </>
  ),
};

export const LargeCount: Story = {
  render: () => (
    <EventRate
      loading={false}
      error={null}
      onRetry={() => {}}
      days={RATE_DAYS.map((d) => d * 10_000)}
      total={RATE_TOTAL * 10_000}
    />
  ),
};

export const Loading: Story = {
  render: () => <EventRate days={[]} total={0} loading error={null} onRetry={() => {}} />,
};
export const QueryFailed: Story = {
  render: () => (
    <EventRate
      days={[]}
      total={0}
      loading={false}
      error={{ name: "Error", message: "Permission denied while loading this section" }}
      onRetry={() => {}}
    />
  ),
};
