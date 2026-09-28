/**
 * Hand-typed fixtures for the events, downloads and platform-activity
 * screens (group "events-downloads").
 */
import { fakeController } from "@/components/data-table/fixtures";
import type { CursorTableController } from "@/components/data-table";
import { PLATFORMS, type PlatformAsset } from "@/components/screens/downloads/platforms";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

import type { AggregatedEvent } from "./use-events";

/** The JSON scalar is typed as a record but carries any JSON value. */
const json = (value: unknown) => value as Record<string, unknown>;

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();

function event(overrides: Partial<AstroliftEvent> & Pick<AstroliftEvent, "id">): AstroliftEvent {
  return {
    eventType: "app.deployed",
    occurredAt: hoursAgo(1),
    organizationId: "org_01J8ZK3Q4M",
    payload: json({ app_slug: "checkout-api", revision: 42 }),
    projectId: null,
    registeredAppId: null,
    resourceId: "checkout-api",
    resourceKind: "app",
    severity: "info",
    teamId: null,
    ...overrides,
  };
}

export const EVENTS: AstroliftEvent[] = [
  event({ id: "0b6f1d2e-7a41-4c8e-9a1f-1c2d3e4f5a61", registeredAppId: "app_7Q2M" }),
  event({
    id: "1c7a2e3f-8b52-4d9f-ab20-2d3e4f5a6b72",
    eventType: "workload.unhealthy",
    occurredAt: hoursAgo(3),
    resourceKind: "workload",
    resourceId: "web",
    payload: json({ app_slug: "checkout-api", workload_slug: "web", reason: "CrashLoopBackOff" }),
  }),
  event({
    id: "2d8b3f40-9c63-4ea0-bc31-3e4f5a6b7c83",
    eventType: "cluster.connected",
    occurredAt: hoursAgo(30),
    resourceKind: "cluster",
    resourceId: "prod-us-east-2",
    payload: json({}),
  }),
  event({
    id: "3e9c4051-ad74-4fb1-cd42-4f5a6b7c8d94",
    eventType: "billing.invoice.issued",
    occurredAt: hoursAgo(80),
    resourceKind: "",
    resourceId: "",
    payload: json({ amount: 1299, currency: "USD", lines: [1, 2, 3] }),
  }),
];

const LONG = "a-really-long-resource-identifier-that-keeps-going-".repeat(4) + "end";

export const EVENTS_LONG: AstroliftEvent[] = [
  event({
    id: "4fad5162-be85-40c2-de53-5a6b7c8d9ea5",
    eventType: `managed_service.backup.completed.${"with-a-very-long-suffix-".repeat(4)}done`,
    resourceKind: "managed_service",
    resourceId: LONG,
    organizationId: `org_${LONG}`,
    teamId: `team_${LONG}`,
    projectId: `project_${LONG}`,
    registeredAppId: `app_${LONG}`,
    payload: json({
      app_slug: "checkout-api",
      managed_service_id: LONG,
      note: "x".repeat(900),
      nested: { deeper: { deepest: ["one", "two", "three"] } },
    }),
  }),
];

export const BUCKETS: AggregatedEvent[] = [
  {
    representative: EVENTS[0],
    count: 12,
    firstAt: hoursAgo(5),
    lastAt: hoursAgo(1),
    eventType: "app.deployed",
    resourceKind: "app",
    resourceId: "checkout-api",
  },
  {
    representative: EVENTS[1],
    count: 1,
    firstAt: hoursAgo(3),
    lastAt: hoursAgo(3),
    eventType: "workload.unhealthy",
    resourceKind: "workload",
    resourceId: "web",
  },
  {
    representative: EVENTS[2],
    count: 3,
    firstAt: hoursAgo(40),
    lastAt: hoursAgo(30),
    eventType: "cluster.connected",
    resourceKind: "cluster",
    resourceId: "prod-us-east-2",
  },
];

export const BUCKETS_LONG: AggregatedEvent[] = [
  {
    representative: EVENTS_LONG[0],
    count: 9999,
    firstAt: hoursAgo(200),
    lastAt: hoursAgo(1),
    eventType: EVENTS_LONG[0].eventType,
    resourceKind: "managed_service",
    resourceId: LONG,
  },
];

/** Members of BUCKETS[0], as the member fetch returns them. */
export const BUCKET_MEMBERS: AstroliftEvent[] = [
  EVENTS[0],
  event({ id: "5abe6273-cf96-41d3-ef64-6b7c8d9eafb6", occurredAt: hoursAgo(2) }),
  event({
    id: "6bcf7384-d0a7-42e4-f075-7c8d9eafb0c7",
    occurredAt: hoursAgo(5),
    payload: json({}),
  }),
];

export function eventsTable(
  overrides: Partial<CursorTableController<AstroliftEvent>> = {}
): CursorTableController<AstroliftEvent> {
  return fakeController<AstroliftEvent>({ rows: EVENTS, sort: undefined, ...overrides });
}

export function bucketsTable(
  overrides: Partial<CursorTableController<AggregatedEvent>> = {}
): CursorTableController<AggregatedEvent> {
  return fakeController<AggregatedEvent>({
    rows: BUCKETS,
    totalCount: BUCKETS.length,
    sort: undefined,
    ...overrides,
  });
}

/** Fourteen days, oldest first. */
export const RATE_DAYS = [3, 5, 2, 0, 8, 12, 7, 4, 9, 15, 11, 6, 10, 14];
export const RATE_TOTAL = RATE_DAYS.reduce((a, b) => a + b, 0);

export const TABLE_ERROR = new globalThis.Error("upstream timed out");

// ─── downloads ─────────────────────────────────────────────────────────

export const DETECTED_PLATFORM: PlatformAsset = PLATFORMS[0];

export const DETECTED_PLATFORM_LONG: PlatformAsset = {
  ...PLATFORMS[2],
  label: `Linux · amd64 · ${"an-unusually-long-distribution-name-".repeat(3)}edition`,
  filename: `astro-linux-amd64-${"very-long-build-suffix-".repeat(5)}.tar.gz`,
  installLine: `gh release download --repo calliopeai/astrolift-cli --pattern 'astro-linux-amd64-${"very-long-build-suffix-".repeat(5)}.tar.gz' --pattern 'astro-checksums.txt'`,
};

// ─── platform activity ─────────────────────────────────────────────────

export const TEMPORAL_UI_URL = "https://temporal.example.internal";
