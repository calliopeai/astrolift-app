import type { AstroliftEvent } from "@/graphql/operations/operations.types";
import type {
  AstroliftManagedServiceConnection,
  AstroliftManagedServiceObjects,
  AstroliftManagedServiceQueueDepth,
} from "@/graphql/services/services.types";

import type { ActivityTimelineViewProps } from "./ActivityTimeline";
import type { DeployStrategyApp, DeployStrategyCardViewProps } from "./DeployStrategyCard";
import type { SummaryService } from "./use-managed-services-summary";

/**
 * Hand-typed fixtures for the app overview cards: deploy strategy,
 * activity timeline, managed-services summary and its dialogs.
 */

const yes = async () => true;
const noop = () => {};

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

// ─── deploy strategy ────────────────────────────────────────────────

export const STRATEGY_APP: DeployStrategyApp = {
  triggerMode: "auto_on_push",
  deployBranch: "main",
  defaultBranch: "main",
  previewEnabled: true,
  cronExpression: "",
};

export const DEPLOY_STRATEGY: DeployStrategyCardViewProps = {
  app: STRATEGY_APP,
  saving: false,
  onSave: yes,
};

// ─── activity timeline ──────────────────────────────────────────────

function event(
  id: string,
  eventType: string,
  minutesAgo: number,
  payload: Record<string, unknown>
): AstroliftEvent {
  return {
    id,
    eventType,
    occurredAt: ago(minutesAgo),
    payload,
    resourceId: "app-1",
    resourceKind: "registered_app",
    severity: "info",
    organizationId: null,
    projectId: null,
    registeredAppId: "app-1",
    teamId: null,
  };
}

export const EVENTS: AstroliftEvent[] = [
  event("ev-1", "deployment.succeeded", 4, { imageTag: "web:3f9c2a1", status: "succeeded" }),
  event("ev-2", "manifest.updated", 38, { summary: "Manifest synced from main" }),
  event("ev-3", "secret.rotated", 120, { message: "DATABASE_URL rotated" }),
  event("ev-4", "app.deploy_token.created", 300, { title: "Deploy token created for CI" }),
  event("ev-5", "alert.fired", 900, { description: "p95 latency above 800ms for 5 minutes" }),
  event("ev-6", "environment.created", 2000, {}),
];

export const ACTIVITY: ActivityTimelineViewProps = {
  events: EVENTS,
  loading: false,
};

/** A long history: older pages still on the cursor. */
export const ACTIVITY_PAGED: ActivityTimelineViewProps = {
  ...ACTIVITY,
  events: Array.from({ length: 40 }, (_, i) =>
    event(`ev-p${i}`, i % 3 === 0 ? "deployment.succeeded" : "manifest.updated", i * 90, {
      summary: `Event ${i + 1}`,
    })
  ),
  hasMore: true,
  onLoadMore: () => {},
};

export const ACTIVITY_LONG: ActivityTimelineViewProps = {
  ...ACTIVITY,
  events: [
    event("ev-l1", "deployment.failed", 2, { summary: `Rollout of ${LONG} failed: ${LONG}` }),
    event("ev-l2", `config.${LONG.replace(/-/g, "_")}`, 10, {}),
  ],
};

// ─── managed services summary ───────────────────────────────────────

function service(over: Partial<SummaryService> & Pick<SummaryService, "id" | "name" | "kind">) {
  return {
    variant: null,
    status: "active",
    statusError: null,
    environmentName: "production",
    registeredAppSlug: "storefront",
    lastActionAt: null,
    lastActionKind: null,
    ...over,
  } as SummaryService;
}

export const SERVICES: SummaryService[] = [
  service({
    id: "ms-1",
    name: "orders-db",
    kind: "postgres",
    variant: "rds",
    status: "failed",
    statusError: "Provisioning timed out waiting for the subnet group.",
  }),
  service({ id: "ms-2", name: "jobs", kind: "queue", variant: "sqs", status: "provisioning" }),
  service({
    id: "ms-3",
    name: "assets",
    kind: "object_store",
    variant: "s3",
    lastActionAt: ago(15),
    lastActionKind: "connection.reveal",
  }),
  service({
    id: "ms-4",
    name: "mailer",
    kind: "email",
    variant: "ses",
    lastActionAt: ago(60 * 26),
    lastActionKind: "test_email.send",
  }),
  service({ id: "ms-5", name: "cache", kind: "redis", environmentName: "staging" }),
  service({ id: "ms-6", name: "models", kind: "model_endpoint" }),
];

export const SERVICES_LONG: SummaryService[] = [
  service({
    id: "ms-l1",
    name: LONG,
    kind: "postgres",
    variant: "aurora-serverless-v2",
    environmentName: LONG,
    status: "failed",
    statusError: LONG,
    lastActionAt: ago(5),
    lastActionKind: `custom.${LONG}`,
  }),
];

export const MANAGED_SERVICES_HREF = "/apps/storefront/managed-services";

// ─── dialogs ────────────────────────────────────────────────────────

export const POSTGRES = SERVICES[0];
export const QUEUE = SERVICES[1];
export const OBJECT_STORE = SERVICES[2];
export const EMAIL = SERVICES[3];

export const CONNECTION: AstroliftManagedServiceConnection = {
  managedServiceId: POSTGRES.id,
  name: POSTGRES.name,
  kind: POSTGRES.kind,
  environmentName: POSTGRES.environmentName,
  connectionSecretRef: "secret-ref:apps/storefront/production/orders-db",
  revealedAt: ago(0),
  keys: [
    {
      key: "DATABASE_HOST",
      value: "orders-db.cluster-abc.us-west-2.rds.amazonaws.com",
      isSecret: false,
    },
    { key: "DATABASE_PORT", value: "5432", isSecret: false },
    { key: "DATABASE_USER", value: "storefront", isSecret: false },
    { key: "DATABASE_PASSWORD", value: "placeholder:pending", isSecret: true },
  ],
};

export const OBJECTS: AstroliftManagedServiceObjects = {
  managedServiceId: OBJECT_STORE.id,
  name: OBJECT_STORE.name,
  kind: OBJECT_STORE.kind,
  cacheAgeSeconds: 42,
  truncated: true,
  objects: [
    { key: "uploads/2026/09/hero.png", sizeBytes: 482_113, lastModified: ago(3) },
    { key: "uploads/2026/09/catalog.csv", sizeBytes: 12_400_000, lastModified: ago(90) },
    { key: "robots.txt", sizeBytes: 64, lastModified: null },
  ],
};

export const DEPTH: AstroliftManagedServiceQueueDepth = {
  managedServiceId: QUEUE.id,
  name: QUEUE.name,
  kind: QUEUE.kind,
  depth: 1284,
  inFlight: 17,
  sampledAt: ago(1),
};

export const DIALOG_BASE = { open: true, onOpenChange: noop };
export const REFRESH = noop;
export const COPY = noop;
export const SEND = yes;
