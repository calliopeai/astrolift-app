import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { NotificationsInbox } from "./NotificationsInbox";
import {
  LONG_NOTIFICATIONS,
  NOTIFICATIONS,
  inboxProps,
} from "./settings-security-notifications.fixtures";

const meta: Meta = { title: "Screens/Settings/Security/NotificationsInbox" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <NotificationsInbox {...inboxProps()} /> };

export const Loading: Story = {
  render: () => <NotificationsInbox {...inboxProps({ notifications: [], loading: true })} />,
};

export const Empty: Story = {
  render: () => <NotificationsInbox {...inboxProps({ notifications: [] })} />,
};

/** Everything read: no "Mark all as read", no per-row button. */
export const AllRead: Story = {
  render: () => (
    <NotificationsInbox {...inboxProps({ notifications: NOTIFICATIONS.filter((n) => n.readAt) })} />
  ),
};

export const LongStrings: Story = {
  render: () => <NotificationsInbox {...inboxProps({ notifications: LONG_NOTIFICATIONS })} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <NotificationsInbox {...inboxProps({ notifications: LONG_NOTIFICATIONS })} />
    </div>
  ),
};
