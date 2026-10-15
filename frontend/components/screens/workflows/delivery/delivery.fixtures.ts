import type { DeliveryRecord } from "./DeliveryChannelPicker";
import type { DeliveryChannel } from "./DeliveryChannelsPanel";

export const TEAMS_CHANNEL: DeliveryChannel = {
  guid: "0192a000-0000-7000-8000-000000000001",
  name: "Ops Teams channel",
  kind: "teams_webhook",
  slackChannel: "",
  emailRecipients: [],
  isActive: true,
  hasCredential: true,
};

export const SLACK_APP_CHANNEL: DeliveryChannel = {
  guid: "0192a000-0000-7000-8000-000000000002",
  name: "#data-digests",
  kind: "slack_app",
  slackChannel: "C0123456789",
  emailRecipients: [],
  isActive: true,
  hasCredential: true,
};

export const EMAIL_CHANNEL: DeliveryChannel = {
  guid: "0192a000-0000-7000-8000-000000000003",
  name: "Finance leads",
  kind: "email",
  slackChannel: "",
  emailRecipients: ["cfo@example.com", "controller@example.com"],
  isActive: false,
  hasCredential: true,
};

export const BROKEN_CHANNEL: DeliveryChannel = {
  guid: "0192a000-0000-7000-8000-000000000004",
  name: "Old Slack webhook",
  kind: "slack_webhook",
  slackChannel: "",
  emailRecipients: [],
  isActive: true,
  hasCredential: false,
};

export const CHANNELS = [TEAMS_CHANNEL, SLACK_APP_CHANNEL, EMAIL_CHANNEL];

export const LONG_CHANNEL: DeliveryChannel = {
  ...EMAIL_CHANNEL,
  guid: "0192a000-0000-7000-8000-000000000005",
  name: "Quarterly revenue reconciliation digest for the regional finance leadership group",
  emailRecipients: Array.from(
    { length: 8 },
    (_, i) => `regional-finance-lead-${i}@subsidiary.example.com`
  ),
  isActive: true,
};

export const RECENT: DeliveryRecord[] = [
  {
    guid: "0192a000-0000-7000-8000-0000000000a1",
    channelName: "Ops Teams channel",
    purpose: "result",
    runOutcome: "completed",
    status: "delivered",
    attempts: 1,
    lastError: "",
    createdAt: "2026-10-15T07:00:12Z",
  },
  {
    guid: "0192a000-0000-7000-8000-0000000000a2",
    channelName: "#data-digests",
    purpose: "result",
    runOutcome: "failed",
    status: "failed",
    attempts: 5,
    lastError: "Slack refused the post: not_in_channel",
    createdAt: "2026-10-14T07:00:09Z",
  },
  {
    guid: "0192a000-0000-7000-8000-0000000000a3",
    channelName: null,
    purpose: "owner_notice",
    runOutcome: "failed",
    status: "delivered",
    attempts: 1,
    lastError: "",
    createdAt: "2026-10-14T07:00:10Z",
  },
];
