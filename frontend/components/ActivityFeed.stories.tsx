import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ActivityFeed } from "@/components/ActivityFeed";
import type { AstroliftActivityItem } from "@/graphql/operations/operations.types";

/** Home's activity feed: what happened, who did it, a link to it. */
const meta: Meta = { title: "Patterns/ActivityFeed" };
export default meta;

const item = (
  id: string,
  action: string,
  targetLabel: string,
  actorDisplay: string,
  minutes: number
): AstroliftActivityItem =>
  ({
    id,
    action,
    actorDisplay,
    eventType: `deploy.${action}`,
    targetKind: "deployment",
    targetLabel,
    targetHref: "#",
    payload: {},
    occurredAt: new Date(Date.UTC(2026, 8, 28, 12, 0) - minutes * 60_000).toISOString(),
  }) as AstroliftActivityItem;

const BASE = {
  loading: false,
  error: null,
  hasMore: false,
  loadingMore: false,
  onLoadMore: () => {},
};

export const Items: StoryObj = {
  render: () => (
    <div className="max-w-xl">
      <ActivityFeed
        {...BASE}
        hasMore
        items={[
          item("1", "succeeded", "checkout → production", "leo", 2),
          item("2", "failed", "billing-api → staging", "astrolift-bot", 14),
          item("3", "approved", "checkout → production", "eric", 40),
        ]}
      />
    </div>
  ),
};
export const Loading: StoryObj = { render: () => <ActivityFeed {...BASE} loading items={[]} /> };
export const Empty: StoryObj = { render: () => <ActivityFeed {...BASE} items={[]} /> };
export const Error: StoryObj = {
  render: () => <ActivityFeed {...BASE} error="upstream timed out" items={[]} />,
};

export const Japanese: StoryObj = {
  ...Items,
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="ja"
        messages={ja}
        timeZone="UTC"
        now={new Date("2026-09-28T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
