import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftRole, AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import type {
  AstroliftAppEnvironment,
  AstroliftDeregisterPreview,
  AstroliftForceRedeployPreview,
} from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";
import type { AstroliftManagedService } from "@/graphql/services/services.types";

import type { AppMembersScreenProps } from "./AppMembersScreen";
import type { AppSettingsScreenProps } from "./AppSettingsScreen";
import type { ArchiveAppViewProps } from "./ArchiveApp";
import type { DangerZoneViewProps } from "./DangerZone";
import type { EnvironmentSettingsViewProps } from "./EnvironmentSettings";
import type { ForceRedeployViewProps } from "./ForceRedeploy";
import type { IngressControlsViewProps } from "./IngressControls";
import type { ManagedServicesAdminViewProps } from "./ManagedServicesAdmin";
import type { ResyncSourceViewProps } from "./ResyncSource";
import type { RetentionPolicyViewProps } from "./RetentionPolicy";
import type { RunScheduledJobViewProps } from "./RunScheduledJob";
import type { WebhookDeploysPauseViewProps } from "./WebhookDeploysPause";

/**
 * Hand-typed fixtures for the app settings landing, its sections, and the
 * app members tab. Records carry only the fields these views read.
 */

const noop = async () => {};
const yes = async () => true;

export const LONG =
  "platform-team-shared-production-checkout-service-with-a-deliberately-long-name-that-keeps-going";

// ─── settings landing ─────────────────────────────────────────────────────────

export const APP = {
  id: "app-1",
  slug: "checkout",
  name: "Checkout",
  sourceRepo: "acme/checkout",
  deployBranch: "main",
  isArchived: false,
  archivedAt: null,
  lastResyncAt: "2026-09-28T09:40:00Z",
  webhookDeploysPaused: false,
  webhookDeploysPausedAt: null,
  webhookDeploysPausedByEmail: null,
  webhookDeploysPauseReason: "",
  retentionPolicies: [{ id: "rp-1", signal: "logs", retentionDays: 14 }],
  settingsLastModified: {
    deployStrategy: "2026-09-27T12:00:00Z",
    deployTokens: "2026-09-20T12:00:00Z",
    secrets: "2026-09-28T08:00:00Z",
    managedServices: null,
    domains: "2026-08-30T12:00:00Z",
    webhooks: null,
    members: "2026-09-01T12:00:00Z",
    observability: null,
  },
} as unknown as AstroliftRegisteredApp;

export const SETTINGS: AppSettingsScreenProps = {
  app: APP,
  loading: false,
  slug: "checkout",
  basePath: "/apps",
};

// ─── resync ───────────────────────────────────────────────────────────────────

export const RESYNC: ResyncSourceViewProps = {
  loading: false,
  onResync: noop,
  lastResyncAt: "2026-09-28T09:40:00Z",
};

// ─── webhook deploys ──────────────────────────────────────────────────────────

export const WEBHOOK_DEPLOYS: WebhookDeploysPauseViewProps = {
  pausing: false,
  resuming: false,
  onPause: yes,
  onResume: noop,
  paused: false,
  pausedAt: null,
  pausedByEmail: null,
  pauseReason: "",
};

export const WEBHOOK_DEPLOYS_PAUSED: WebhookDeploysPauseViewProps = {
  ...WEBHOOK_DEPLOYS,
  paused: true,
  pausedAt: "2026-09-28T09:10:00Z",
  pausedByEmail: "leo@example.com",
  pauseReason: "CI is looping on a flaky migration; stopping deploys while we fix it.",
};

// ─── ingress ──────────────────────────────────────────────────────────────────

function environment(
  name: string,
  patch: Partial<AstroliftAppEnvironment> = {}
): AstroliftAppEnvironment {
  return {
    id: `env-${name}`,
    name,
    ingressPaused: false,
    settings: [],
    ...patch,
  } as AstroliftAppEnvironment;
}

export const ENVIRONMENTS: AstroliftAppEnvironment[] = [
  environment("production", {
    settings: [
      { id: "s-1", key: "replicas", value: "3" },
      { id: "s-2", key: "memory_limit", value: "1Gi" },
    ],
  } as Partial<AstroliftAppEnvironment>),
  environment("staging", { ingressPaused: true }),
];

export const INGRESS: IngressControlsViewProps = {
  envs: ENVIRONMENTS,
  loading: false,
  busyIds: [],
  onToggle: noop,
};

// ─── managed services admin ───────────────────────────────────────────────────

function service(
  id: string,
  patch: Partial<AstroliftManagedService> = {}
): AstroliftManagedService {
  return {
    id,
    name: "orders-db",
    kind: "postgres",
    variant: "rds",
    environmentName: "production",
    status: "active",
    statusError: "",
    editableFields: ["instance_class", "backup_retention_days"],
    config: { instance_class: "db.t4g.medium", backup_retention_days: 7 },
    ...patch,
  } as AstroliftManagedService;
}

export const SERVICES: AstroliftManagedService[] = [
  service("ms-1"),
  service("ms-2", {
    name: "sessions",
    kind: "redis",
    variant: "",
    status: "provisioning",
    editableFields: [],
    config: {},
  }),
  service("ms-3", {
    name: "uploads",
    kind: "s3",
    variant: "",
    environmentName: "staging",
    status: "failed",
    statusError: "AccessDenied: the provisioner role cannot call s3:CreateBucket in us-west-2.",
    editableFields: ["versioning"],
    config: { versioning: false },
  }),
];

export const MANAGED_SERVICES_ADMIN: ManagedServicesAdminViewProps = {
  services: SERVICES,
  loading: false,
  reprovisioning: false,
  updating: false,
  onReprovision: yes,
  onSave: yes,
};

// ─── retention ────────────────────────────────────────────────────────────────

export const RETENTION: RetentionPolicyViewProps = {
  saving: {},
  onChange: noop,
  policies: [
    { id: "rp-1", signal: "logs", retentionDays: 14 },
    { id: "rp-2", signal: "audit_events", retentionDays: 365 },
  ],
};

// ─── environment overrides ────────────────────────────────────────────────────

export const ENV_SETTINGS: EnvironmentSettingsViewProps = {
  envs: ENVIRONMENTS,
  adding: false,
  onAdd: yes,
  onClear: noop,
};

// ─── archive ──────────────────────────────────────────────────────────────────

export const ARCHIVE: ArchiveAppViewProps = {
  archiving: false,
  restoring: false,
  onArchive: yes,
  onRestore: noop,
  appName: "Checkout",
  isArchived: false,
  archivedAt: null,
};

// ─── force redeploy ───────────────────────────────────────────────────────────

export const FORCE_REDEPLOY_PREVIEW = {
  appSlug: "checkout",
  environmentName: null,
  inFlightDeployments: [
    {
      id: "dep-1",
      status: "deploying",
      environmentName: "production",
      workloadSlug: "web",
      imageTag: "sha-9f3c2a1",
      triggeredByDisplay: "leo@example.com",
      triggerKind: "push",
      ciActorKind: "",
      ciRunUrl: "https://github.com/acme/checkout/actions/runs/1",
      createdAt: "2026-09-28T09:50:00Z",
      startedAt: "2026-09-28T09:51:00Z",
    },
  ],
} as AstroliftForceRedeployPreview;

export const FORCE_REDEPLOY: ForceRedeployViewProps = {
  appSlug: "checkout",
  loading: false,
  preview: FORCE_REDEPLOY_PREVIEW,
  previewLoading: false,
  loadPreview: () => {},
  onForceRedeploy: yes,
};

// ─── danger zone ──────────────────────────────────────────────────────────────

export const DEREGISTER_PREVIEW = {
  appName: "Checkout",
  appSlug: "checkout",
  totalResourceCount: 9,
  k8sObjects: [
    {
      apiVersion: "apps/v1",
      clusterSlug: "prd-us-west-2",
      namespace: "checkout",
      kind: "Deployment",
      name: "web",
    },
    {
      apiVersion: "v1",
      clusterSlug: "prd-us-west-2",
      namespace: "checkout",
      kind: "Service",
      name: "web",
    },
  ],
  managedServices: [
    {
      id: "ms-1",
      name: "orders-db",
      kind: "postgres",
      variant: "rds",
      status: "active",
      environmentName: "production",
    },
  ],
  secretRefs: [
    {
      id: "sr-1",
      bundleSlug: "checkout-prod",
      prefix: "APP_",
      environmentName: "production",
      clusterSlug: "prd-us-west-2",
    },
  ],
  deployTokens: [{ id: "tok-1", name: "github-actions", last4: "a1b2", environmentName: null }],
  identityRoles: [
    {
      clusterSlug: "prd-us-west-2",
      roleArnOrPrincipal: "arn:aws:iam::123456789012:role/checkout-web",
      kind: "irsa",
    },
  ],
  sourceWebhook: { installed: true, repo: "acme/checkout", hookId: "48213" },
  registryRepoUri: "123456789012.dkr.ecr.us-west-2.amazonaws.com/checkout",
} as AstroliftDeregisterPreview;

export const DANGER_ZONE: DangerZoneViewProps = {
  appName: "Checkout",
  loading: false,
  preview: null,
  previewLoading: false,
  stillLive: [],
  loadPreview: () => {},
  onDeregister: yes,
  onRetry: yes,
};

// ─── run scheduled job ────────────────────────────────────────────────────────

export const CRON_JOBS = [
  { id: "wl-2", slug: "nightly-report", kind: "cronjob", schedule: "0 2 * * *" },
  { id: "wl-3", slug: "purge-sessions", kind: "cronjob", schedule: "*/15 * * * *" },
] as AstroliftWorkload[];

export const RUN_JOB: RunScheduledJobViewProps = {
  appSlug: "checkout",
  cronJobs: CRON_JOBS,
  environments: ENVIRONMENTS,
  workloadsLoading: false,
  running: false,
  onRun: noop,
  basePath: "/apps",
};

// ─── members ──────────────────────────────────────────────────────────────────

export const ROLES = [
  { id: "role-1", slug: "app-admin", scopeLevel: "APP" },
  { id: "role-2", slug: "app-deployer", scopeLevel: "APP" },
  { id: "role-3", slug: "app-viewer", scopeLevel: "APP" },
] as AstroliftRole[];

function binding(id: string, patch: Partial<AstroliftRoleBinding> = {}): AstroliftRoleBinding {
  return {
    id,
    user: { id: `u-${id}`, username: "leo", email: "leo@example.com" },
    groupExternalId: "",
    role: ROLES[0],
    grantedAt: "2026-09-01T12:00:00Z",
    expiresAt: null,
    ...patch,
  } as AstroliftRoleBinding;
}

export const BINDINGS: AstroliftRoleBinding[] = [
  binding("rb-1"),
  binding("rb-2", {
    user: { id: "u-2", username: "eric", email: "eric@example.com" },
    role: ROLES[1],
    expiresAt: "2026-12-31T00:00:00Z",
  } as Partial<AstroliftRoleBinding>),
  binding("rb-3", {
    user: null,
    groupExternalId: "okta:platform-oncall",
    role: ROLES[2],
  } as Partial<AstroliftRoleBinding>),
];

export const MEMBERS: AppMembersScreenProps = {
  app: APP,
  loading: false,
  appRoles: ROLES,
  table: fakeController<AstroliftRoleBinding>({ rows: BINDINGS, totalCount: BINDINGS.length }),
  revoking: false,
  onRevoke: noop,
  slug: "checkout",
};
