/**
 * Hand-typed story fixtures for the alerts screens, typed against each
 * screen's props so a story cannot drift from its hook.
 */
import type { AlertEventDetailProps } from "./AlertEventDetail";
import type { AlertEventsScreenProps } from "./AlertEventsScreen";
import type { AlertRuleDetailProps } from "./AlertRuleDetail";
import type { AlertsScreenProps } from "./AlertsScreen";
import type { AlertEvent, AlertRule } from "./use-alerts";

// The JSON scalar is typed as an object, but the server can send any JSON.
const json = (v: unknown) => v as Record<string, unknown>;

// A mute that ends well after any story is viewed, so the badge always shows time left.
const MUTE_UNTIL = "2099-01-01T00:00:00Z";

export const RULES: AlertRule[] = [
  {
    id: "3f1c9a20-5b7e-4d21-9c0a-7e6f5d4c3b2a",
    name: "deploy-failed",
    target: "app",
    targetId: "checkout-api",
    severity: "critical",
    predicate: json({ event: "deployment.failed" }),
    notifyChannels: json({ slack: "#oncall" }),
    isActive: true,
    organizationSlug: "acme",
    createdAt: "2026-08-14T10:00:00Z",
    updatedAt: "2026-09-02T12:30:00Z",
    activeMute: null,
  },
  {
    id: "7a2b4c6d-8e9f-4a1b-b2c3-d4e5f6a7b8c9",
    name: "high-error-rate",
    target: "env",
    targetId: "production",
    severity: "warn",
    predicate: json({ metric: "error_rate", op: ">", value: 0.05 }),
    notifyChannels: json({ slack: "#platform", email: ["sre@example.com"] }),
    isActive: true,
    organizationSlug: "acme",
    createdAt: "2026-09-01T08:15:00Z",
    updatedAt: "2026-09-01T08:15:00Z",
    activeMute: {
      id: "mute-1",
      ttlUntil: MUTE_UNTIL,
      reason: "Quick mute (4h)",
      createdBy: "ada.lovelace",
    },
  },
  {
    id: "c0ffee00-1234-4abc-8def-0123456789ab",
    name: "cluster-heartbeat",
    target: "global",
    targetId: "",
    severity: "info",
    predicate: json({ event: "agent.heartbeat_missed" }),
    notifyChannels: json({}),
    isActive: false,
    organizationSlug: "acme",
    createdAt: "2026-09-20T08:15:00Z",
    updatedAt: "2026-09-21T09:00:00Z",
    activeMute: null,
  },
];

export const EVENTS: AlertEvent[] = [
  {
    id: "e1a2b3c4-d5e6-4f70-8192-a3b4c5d6e7f8",
    ruleId: RULES[0].id,
    severity: "critical",
    firedAt: "2026-09-28T07:42:00Z",
    resolvedAt: null,
    acknowledgedAt: null,
    summary: "Deployment of checkout-api failed: image pull backoff",
    detail: json({ app: "checkout-api", revision: 42, reason: "ImagePullBackOff" }),
  },
  {
    id: "e2b3c4d5-e6f7-4081-92a3-b4c5d6e7f809",
    ruleId: RULES[1].id,
    severity: "warn",
    firedAt: "2026-09-28T06:10:00Z",
    resolvedAt: null,
    acknowledgedAt: "2026-09-28T06:20:00Z",
    summary: "Error rate 7.2% on production",
    detail: json({ errorRate: 0.072, window: "5m" }),
  },
  {
    id: "e3c4d5e6-f708-4192-a3b4-c5d6e7f8091a",
    ruleId: RULES[2].id,
    severity: "info",
    firedAt: "2026-09-27T22:00:00Z",
    resolvedAt: "2026-09-27T22:05:00Z",
    acknowledgedAt: null,
    summary: "Agent heartbeat missed on conflict-astro",
    detail: json({}),
  },
];

const LONG = "a-very-long-identifier-that-keeps-going-well-past-any-sensible-column-width";

export const LONG_RULE: AlertRule = {
  ...RULES[1],
  id: "9d8c7b6a-5f4e-4d3c-b2a1-0f9e8d7c6b5a",
  name: `production-error-rate-over-five-percent-${LONG}`,
  targetId: `production-${LONG}`,
  organizationSlug: `acme-corporation-international-holdings-${LONG}`,
  activeMute: {
    id: "mute-long",
    ttlUntil: MUTE_UNTIL,
    reason: `Maintenance window for the database migration that keeps running ${LONG}`,
    createdBy: `ada.augusta.king.countess.of.lovelace.${LONG}`,
  },
};

export const LONG_EVENT: AlertEvent = {
  ...EVENTS[0],
  id: "f9e8d7c6-b5a4-4938-8271-605f4e3d2c1b",
  ruleId: LONG_RULE.id,
  summary: `Deployment of checkout-api failed because the image could not be pulled ${LONG}`,
  detail: json({ reason: `ImagePullBackOff ${LONG}`, nested: { list: [1, 2, 3], key: LONG } }),
};

const noopAsync = async () => {};

/** The rules screen's props less the list controller, which the story builds. */
export function alertsProps(
  overrides: Partial<Omit<AlertsScreenProps, "list">> = {}
): Omit<AlertsScreenProps, "list"> {
  return {
    rows: RULES,
    totalCount: RULES.length,
    nextCursor: null,
    loading: false,
    error: null,
    onRetry: () => {},
    activeRuleCount: 2,
    unresolvedCount: 2,
    busy: false,
    createRule: async () => true,
    deleteRule: noopAsync,
    mutePreset: noopAsync,
    muteCustom: async () => true,
    unmute: noopAsync,
    ...overrides,
  };
}

/** The events feed screen's props. */
export function alertEventsProps(
  overrides: Partial<AlertEventsScreenProps> = {},
  feed: Partial<AlertEventsScreenProps["events"]> = {}
): AlertEventsScreenProps {
  return {
    view: "all",
    busy: false,
    acknowledge: noopAsync,
    ...overrides,
    events: {
      items: EVENTS,
      loading: false,
      error: null,
      onRetry: () => {},
      hasMore: false,
      loadingMore: false,
      onLoadMore: () => undefined,
      newCount: 0,
      onShowNew: () => {},
      ...feed,
    },
  };
}

export const RULE_DETAIL: AlertRuleDetailProps = {
  id: RULES[1].id,
  rule: { ...RULES[1], managedServiceId: "ms-postgres-7f3c" },
  loading: false,
};

export const EVENT_DETAIL: AlertEventDetailProps = {
  id: EVENTS[0].id,
  event: EVENTS[0],
  loading: false,
};
