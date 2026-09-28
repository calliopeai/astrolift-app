import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AlertSubscriptionsView } from "./AlertSubscriptions";
import { NotificationsScreen } from "./Notifications";
import {
  LONG_NOTIFICATIONS,
  NOTIFICATIONS,
  alertProps,
  notificationsProps,
} from "./settings-security-notifications.fixtures";

const meta: Meta = {
  title: "Screens/Settings/Security/Notifications",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const alerts = <AlertSubscriptionsView {...alertProps()} />;

export const Full: Story = {
  render: () => <NotificationsScreen {...notificationsProps({ alertSubscriptions: alerts })} />,
};

export const Loading: Story = {
  render: () => (
    <NotificationsScreen
      {...notificationsProps({
        notifications: [],
        loading: true,
        alertSubscriptions: (
          <AlertSubscriptionsView {...alertProps({ rows: [], state: "loading" })} />
        ),
      })}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <NotificationsScreen
      {...notificationsProps({ notifications: [], alertSubscriptions: alerts })}
    />
  ),
};

/** Everything read: no "Mark all as read", no per-row button. */
export const AllRead: Story = {
  render: () => (
    <NotificationsScreen
      {...notificationsProps({
        notifications: NOTIFICATIONS.filter((n) => n.readAt),
        alertSubscriptions: alerts,
      })}
    />
  ),
};

/**
 * The inbox has no error state of its own (a failed query renders as empty);
 * this is the closest real one: the alert matrix failing above an empty inbox.
 */
export const AlertsLoadFailed: Story = {
  render: () => (
    <NotificationsScreen
      {...notificationsProps({
        notifications: [],
        alertSubscriptions: (
          <AlertSubscriptionsView
            {...alertProps({
              rows: [],
              state: "error",
              error: new Error("Response not successful: Received status code 503"),
            })}
          />
        ),
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <NotificationsScreen
      {...notificationsProps({ notifications: LONG_NOTIFICATIONS, alertSubscriptions: alerts })}
    />
  ),
};
