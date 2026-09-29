import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import type * as React from "react";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

import { ALERT_SUBSCRIPTIONS_LIST } from "./alert-subscriptions-list";
import { AlertSubscriptionsView, type AlertSubscriptionsViewProps } from "./AlertSubscriptions";
import { NotificationsScreen } from "./Notifications";
import { NotificationsInbox } from "./NotificationsInbox";
import {
  LONG_NOTIFICATIONS,
  alertProps,
  inboxProps,
} from "./settings-security-notifications.fixtures";

const meta: Meta = {
  title: "Screens/Settings/Security/Notifications",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function AlertSubscriptionsStory({
  initial,
  ...over
}: Partial<Omit<AlertSubscriptionsViewProps, "list">> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(ALERT_SUBSCRIPTIONS_LIST, initial);
  return <AlertSubscriptionsView {...alertProps(over)} list={list} />;
}

function Screen({
  initial = null,
  inbox = <NotificationsInbox {...inboxProps()} />,
  alerts = <AlertSubscriptionsStory />,
}: {
  initial?: string | null;
  inbox?: React.ReactNode;
  alerts?: React.ReactNode;
}) {
  const section = useLocalSettingsSection(initial);
  return <NotificationsScreen section={section} inbox={inbox} alertSubscriptions={alerts} />;
}

/** The inbox section, the default. */
export const Full: Story = { render: () => <Screen /> };

/** The alert subscriptions section: only it is mounted. */
export const AlertsSection: Story = { render: () => <Screen initial="alerts" /> };

export const Loading: Story = {
  render: () => (
    <Screen inbox={<NotificationsInbox {...inboxProps({ notifications: [], loading: true })} />} />
  ),
};

export const Empty: Story = {
  render: () => <Screen inbox={<NotificationsInbox {...inboxProps({ notifications: [] })} />} />,
};

/** The alert list failed; the inbox is not affected, it is not mounted. */
export const AlertsLoadFailed: Story = {
  render: () => (
    <Screen
      initial="alerts"
      alerts={
        <AlertSubscriptionsStory
          rows={[]}
          error={{ message: "Response not successful: Received status code 503" }}
        />
      }
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Screen inbox={<NotificationsInbox {...inboxProps({ notifications: LONG_NOTIFICATIONS })} />} />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen
        inbox={<NotificationsInbox {...inboxProps({ notifications: LONG_NOTIFICATIONS })} />}
      />
    </div>
  ),
};
