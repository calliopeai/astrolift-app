import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { NotificationsBell } from "@/components/NotificationsBell";
import type { AstroliftNotification } from "@/graphql/operations/operations.types";

const meta: Meta = { title: "Shell/NotificationsBell" };
export default meta;

const n = (id: string, title: string, body: string, read = false): AstroliftNotification => ({
  id,
  title,
  body,
  kind: "deploy",
  link: "#",
  userId: "u1",
  createdAt: "2026-09-28T12:00:00Z",
  readAt: read ? "2026-09-28T12:05:00Z" : null,
});

const ACTIONS = { markingAll: false, onMarkRead: () => {}, onMarkAll: () => {} };

export const Unread: StoryObj = {
  render: () => (
    <NotificationsBell
      {...ACTIONS}
      loading={false}
      notifications={[
        n("1", "Deploy waiting on you", "checkout → production needs an approval"),
        n("2", "Run failed", "support-bot run 7e11 failed: tool timeout"),
        n("3", "Deploy succeeded", "billing-api → staging", true),
      ]}
    />
  ),
};
export const Empty: StoryObj = {
  render: () => <NotificationsBell {...ACTIONS} loading={false} notifications={[]} />,
};
export const Loading: StoryObj = {
  render: () => <NotificationsBell {...ACTIONS} loading notifications={[]} />,
};
