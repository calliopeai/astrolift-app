import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { alertEventsProps, EVENTS, LONG_EVENT } from "./alerts.fixtures";
import { AlertEventsScreen } from "./AlertEventsScreen";

const meta: Meta = {
  title: "Screens/Alerts/AlertEventsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <AlertEventsScreen {...alertEventsProps()} /> };

/** Firing: the unresolved ones only. */
export const Firing: Story = {
  render: () => (
    <AlertEventsScreen
      {...alertEventsProps({ view: "firing" }, { items: EVENTS.filter((e) => !e.resolvedAt) })}
    />
  ),
};

/** Older events behind the cursor: Load older at the end of the frame. */
export const HasOlder: Story = {
  render: () => <AlertEventsScreen {...alertEventsProps({}, { hasMore: true })} />,
};

/** New events arrived above the ones being read. */
export const NewEvents: Story = {
  render: () => <AlertEventsScreen {...alertEventsProps({}, { newCount: 3 })} />,
};

export const Loading: Story = {
  render: () => <AlertEventsScreen {...alertEventsProps({}, { items: [], loading: true })} />,
};

export const Empty: Story = {
  render: () => <AlertEventsScreen {...alertEventsProps({}, { items: [] })} />,
};

export const ErrorState: Story = {
  render: () => (
    <AlertEventsScreen {...alertEventsProps({}, { items: [], error: "upstream timed out" })} />
  ),
};

export const Busy: Story = {
  render: () => <AlertEventsScreen {...alertEventsProps({ busy: true })} />,
};

export const LongStrings: Story = {
  render: () => <AlertEventsScreen {...alertEventsProps({}, { items: [LONG_EVENT, ...EVENTS] })} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <AlertEventsScreen {...alertEventsProps({}, { items: [LONG_EVENT, ...EVENTS] })} />
    </div>
  ),
};
