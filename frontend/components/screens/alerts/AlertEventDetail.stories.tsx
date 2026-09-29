import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EVENTS, EVENT_DETAIL, LONG_EVENT } from "./alerts.fixtures";
import { AlertEventDetail } from "./AlertEventDetail";

const meta: Meta = {
  title: "Screens/Alerts/AlertEventDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** A firing event with a detail payload. */
export const Full: Story = { render: () => <AlertEventDetail {...EVENT_DETAIL} /> };

export const Acknowledged: Story = {
  render: () => <AlertEventDetail id={EVENTS[1].id} event={EVENTS[1]} loading={false} />,
};

/** Resolved, with an empty detail payload. */
export const EmptyDetail: Story = {
  render: () => <AlertEventDetail id={EVENTS[2].id} event={EVENTS[2]} loading={false} />,
};

export const Loading: Story = {
  render: () => <AlertEventDetail {...EVENT_DETAIL} event={null} loading />,
};

/**
 * No such event. This is also the closest real state to "error": the screen
 * has no error view, a failed LIST_ALERT_EVENTS lands here.
 */
export const NotFound: Story = {
  render: () => <AlertEventDetail {...EVENT_DETAIL} event={null} />,
};

export const LongStrings: Story = {
  render: () => <AlertEventDetail id={LONG_EVENT.id} event={LONG_EVENT} loading={false} />,
};
