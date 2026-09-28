import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { alertsProps, LONG_EVENT, LONG_RULE } from "./alerts.fixtures";
import { AlertsScreen } from "./AlertsScreen";

const meta: Meta = {
  title: "Screens/Alerts/AlertsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <AlertsScreen {...alertsProps()} /> };

export const Loading: Story = {
  render: () => (
    <AlertsScreen
      {...alertsProps(
        { state: "loading", rows: [], totalCount: null },
        { state: "loading", rows: [], totalCount: null },
        { activeRuleCount: 0, unresolvedCount: 0 }
      )}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <AlertsScreen
      {...alertsProps(
        { state: "empty", rows: [], totalCount: 0 },
        { state: "empty", rows: [], totalCount: 0 },
        { activeRuleCount: 0, unresolvedCount: 0 }
      )}
    />
  ),
};

export const EmptyFiltered: Story = {
  render: () => (
    <AlertsScreen
      {...alertsProps(
        { state: "emptyFiltered", rows: [], isFiltered: true, search: "zzz" },
        { state: "emptyFiltered", rows: [], isFiltered: true, search: "zzz" }
      )}
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <AlertsScreen
      {...alertsProps(
        { state: "error", rows: [], error: new globalThis.Error("upstream timed out") },
        { state: "error", rows: [], error: new globalThis.Error("upstream timed out") }
      )}
    />
  ),
};

/** A mutation in flight: every row action and the Ack buttons are disabled. */
export const Busy: Story = {
  render: () => <AlertsScreen {...alertsProps({}, {}, { busy: true })} />,
};

export const LongStrings: Story = {
  render: () => (
    <AlertsScreen
      {...alertsProps({ rows: [LONG_RULE], totalCount: 1 }, { rows: [LONG_EVENT], totalCount: 1 })}
    />
  ),
};
