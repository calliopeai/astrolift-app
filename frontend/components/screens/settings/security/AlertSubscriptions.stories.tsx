import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AlertSubscriptionsView } from "./AlertSubscriptions";
import { LONG_APPS, alertProps } from "./settings-security-notifications.fixtures";

const meta: Meta = {
  title: "Screens/Settings/Security/AlertSubscriptions",
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <AlertSubscriptionsView {...alertProps()} />,
};

export const Loading: Story = {
  render: () => <AlertSubscriptionsView {...alertProps({ rows: [], state: "loading" })} />,
};

export const Empty: Story = {
  render: () => (
    <AlertSubscriptionsView {...alertProps({ rows: [], totalCount: 0, state: "empty" })} />
  ),
};

/** A filter that matches no app. */
export const NoMatches: Story = {
  render: () => (
    <AlertSubscriptionsView
      {...alertProps({
        rows: [],
        totalCount: 0,
        state: "emptyFiltered",
        search: "zzz",
        isFiltered: true,
      })}
    />
  ),
};

/** The apps page failed to load. */
export const LoadFailed: Story = {
  render: () => (
    <AlertSubscriptionsView
      {...alertProps({
        rows: [],
        state: "error",
        error: new Error("Response not successful: Received status code 503"),
      })}
    />
  ),
};

/** A mutation or the subscription list in flight: every control is disabled. */
export const Busy: Story = {
  render: () => <AlertSubscriptionsView {...alertProps({}, { busy: true })} />,
};

export const LongStrings: Story = {
  render: () => (
    <AlertSubscriptionsView {...alertProps({ rows: LONG_APPS, totalCount: LONG_APPS.length })} />
  ),
};
