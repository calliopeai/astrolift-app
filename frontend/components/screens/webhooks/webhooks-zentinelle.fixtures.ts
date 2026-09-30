/**
 * Hand-typed story fixtures for the webhooks screens (list, subscription
 * detail, delivery detail), typed against each screen's props so a story
 * cannot drift from its hook.
 */
import type {
  AstroliftWebhookDelivery,
  AstroliftWebhookSubscription,
  AstroliftWebhookTestResult,
} from "@/graphql/operations/operations.types";

import type { SubscriptionDetailViewProps } from "./SubscriptionDetail";
import type { SubscriptionDeliveriesData } from "./use-webhooks";
import type { WebhookDeliveryData, WebhookDetailData } from "./use-webhook-detail";
import type { WebhooksScreenProps } from "./WebhooksScreen";

const noop = () => {};
const resolved = async () => {};

export function subscription(
  id: string,
  patch: Partial<AstroliftWebhookSubscription> = {}
): AstroliftWebhookSubscription {
  return {
    id,
    url: "https://collector.acme.dev/astrolift",
    events: ["DEPLOY_STARTED", "DEPLOY_SUCCEEDED", "DEPLOY_FAILED"],
    format: "generic",
    isActive: true,
    failureCount: 0,
    lastDeliveryAt: "2026-09-27T14:05:00Z",
    lastResponseStatus: 200,
    secretRotatedAt: "2026-09-01T12:00:00Z",
    version: 3,
    createdAt: "2026-08-15T09:30:00Z",
    ...patch,
  };
}

export const SUBSCRIPTIONS: AstroliftWebhookSubscription[] = [
  subscription("6f1c2a90-0000-4000-8000-000000000001"),
  subscription("6f1c2a90-0000-4000-8000-000000000002", {
    url: "https://hooks.slack.com/services/T000/B000/XXXX",
    format: "slack",
    events: [
      "APP_REGISTERED",
      "DEPLOY_STARTED",
      "DEPLOY_SUCCEEDED",
      "DEPLOY_FAILED",
      "PREVIEW_CREATED",
      "PREVIEW_TORN_DOWN",
    ],
    failureCount: 4,
    lastResponseStatus: 500,
  }),
  subscription("6f1c2a90-0000-4000-8000-000000000003", {
    url: "https://discord.com/api/webhooks/1234/abcd",
    format: "discord",
    events: ["*"],
    isActive: false,
    lastDeliveryAt: null,
    lastResponseStatus: null,
    secretRotatedAt: null,
  }),
];

export const LONG_SUBSCRIPTIONS: AstroliftWebhookSubscription[] = [
  subscription("6f1c2a90-0000-4000-8000-000000000101", {
    url: "https://a-very-long-subdomain-name-for-the-collector.internal.observability.platform.acme-corporation.example.com/v2/ingest/astrolift/webhooks/production-us-east-1",
    events: [
      "APP_REGISTERED_WITH_A_VERY_LONG_CUSTOM_EVENT_NAME",
      "DEPLOY_STARTED",
      "DEPLOY_SUCCEEDED",
      "DEPLOY_FAILED",
      "PREVIEW_CREATED",
    ],
    failureCount: 128934,
  }),
];

export function delivery(
  id: string,
  patch: Partial<AstroliftWebhookDelivery> = {}
): AstroliftWebhookDelivery {
  return {
    id,
    subscriptionId: SUBSCRIPTIONS[0].id,
    deliveryId: `dlv_${id.slice(0, 8)}`,
    deliveredAt: "2026-09-27T14:05:00Z",
    eventType: "DEPLOY_SUCCEEDED",
    isTest: false,
    latencyMs: 142,
    retryAttempt: 1,
    statusCode: 200,
    success: true,
    error: "",
    requestPayloadExcerpt: '{\n  "event": "DEPLOY_SUCCEEDED",\n  "app": "storefront"\n}',
    responseBodyExcerpt: '{"ok":true}',
    ...patch,
  };
}

export const DELIVERIES: AstroliftWebhookDelivery[] = [
  delivery("a1b2c3d4-0000-4000-8000-000000000001"),
  delivery("a1b2c3d4-0000-4000-8000-000000000002", {
    eventType: "webhook.test",
    isTest: true,
    latencyMs: 88,
  }),
  delivery("a1b2c3d4-0000-4000-8000-000000000003", {
    eventType: "DEPLOY_FAILED",
    statusCode: 500,
    success: false,
    retryAttempt: 3,
    latencyMs: 2013,
    responseBodyExcerpt: "Internal Server Error",
  }),
  delivery("a1b2c3d4-0000-4000-8000-000000000004", {
    eventType: "DEPLOY_STARTED",
    statusCode: null,
    success: false,
    latencyMs: 10000,
    error: "dial tcp 10.0.0.12:443: i/o timeout",
    responseBodyExcerpt: "",
  }),
];

const LONG_TEXT =
  "upstream connect error or disconnect/reset before headers. reset reason: connection termination; ".repeat(
    8
  );

export const LONG_DELIVERY = delivery("a1b2c3d4-0000-4000-8000-000000000999", {
  eventType: "APP_REGISTERED_WITH_A_VERY_LONG_CUSTOM_EVENT_NAME_FOR_TESTING_WRAP",
  deliveryId: "dlv_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  statusCode: 503,
  success: false,
  error: LONG_TEXT,
  requestPayloadExcerpt: JSON.stringify(
    { event: "APP_REGISTERED", app: "a".repeat(200), labels: { team: "platform".repeat(20) } },
    null,
    2
  ),
  responseBodyExcerpt: LONG_TEXT,
});

export const TEST_RESULT_OK: AstroliftWebhookTestResult = {
  delivered: true,
  deliveryId: "dlv_test_7f3c2a91",
  durationMs: 131,
  error: "",
  responseBodyExcerpt: '{"ok":true}',
  statusCode: 200,
  subscriptionId: SUBSCRIPTIONS[0].id,
  timestamp: "2026-09-27T14:06:00Z",
  url: SUBSCRIPTIONS[0].url,
};

export const TEST_RESULT_FAILED: AstroliftWebhookTestResult = {
  ...TEST_RESULT_OK,
  delivered: false,
  deliveryId: "",
  statusCode: null,
  responseBodyExcerpt: "",
  error: "dial tcp 10.0.0.12:443: i/o timeout",
};

/** The list screen, loaded, with nothing revealed or open; the story adds the list state. */
export const WEBHOOKS: Omit<WebhooksScreenProps, "renderDetail" | "list"> = {
  rows: SUBSCRIPTIONS,
  totalCount: SUBSCRIPTIONS.length,
  nextCursor: null,
  loading: false,
  error: null,
  onRetry: noop,
  creating: false,
  pendingRows: new Set(),
  rotating: false,
  firing: false,
  deleting: false,
  reveal: null,
  dismissReveal: noop,
  copySecret: noop,
  testResult: null,
  dismissTestResult: noop,
  onCreate: async () => true,
  onDelete: resolved,
  onToggleActive: resolved,
  onFormatChange: resolved,
  onRotate: resolved,
  onTestFire: resolved,
};

export const REVEAL = {
  plaintextSecret: "whsec_9f8e7d6c5b4a39281706f5e4d3c2b1a0",
  subscription: SUBSCRIPTIONS[0],
};

/** A delivery log's Feed props: the newest page, nothing older. */
export const DELIVERIES_FEED: SubscriptionDeliveriesData["deliveries"] = {
  items: DELIVERIES,
  loading: false,
  error: null,
  onRetry: noop,
  hasMore: false,
  loadingMore: false,
  onLoadMore: () => undefined,
  newCount: 0,
  onShowNew: noop,
};

export const SUBSCRIPTION_DETAIL: SubscriptionDetailViewProps = {
  subscription: SUBSCRIPTIONS[1],
  deliveries: DELIVERIES_FEED,
};

export const WEBHOOK_DETAIL: WebhookDetailData = {
  id: SUBSCRIPTIONS[1].id,
  loading: false,
  subscription: SUBSCRIPTIONS[1],
  deliveries: DELIVERIES_FEED,
};

export const DELIVERY_DETAIL: WebhookDeliveryData = {
  subscriptionId: SUBSCRIPTIONS[0].id,
  deliveryId: DELIVERIES[3].id,
  loading: false,
  delivery: DELIVERIES[3],
};
