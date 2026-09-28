/**
 * Hand-typed fixtures for the Apps list (components/screens/apps/list/).
 * Data only; the screen is fed through `selectApps`, as the hook feeds it.
 */
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import type { TopologyKind } from "@/lib/topology";

import type { AppFreshnessRowProps } from "./AppFreshnessRow";
import type { AppRow } from "./apps-list";
import type { AppsListScreenProps, PushSecretsDialogProps } from "./AppsListScreen";

const noop = () => {};
const asyncTrue = async () => true;

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";
export const SHA64 = "4f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f";
export const ARN200 =
  "arn:aws:iam::123456789012:role/astrolift/platform-team-shared-production-workloads/us-west-2/deploy-role-for-api-gateway-with-a-very-long-path-that-keeps-going-and-going-until-it-reaches-two-hundred-chars";
export const UNBROKEN_URL =
  "https://git.example.com/platform-team-shared-production-workloads/api-gateway-monorepo-with-an-unbroken-path/tree/main/services/edge";

function app(overrides: Partial<AppRow>): AppRow {
  return {
    id: "a0000000-0000-4000-8000-000000000001",
    slug: "api-gateway",
    name: "API Gateway",
    description: "Edge router for every public API, with rate limits and auth.",
    teamSlug: "platform",
    projectSlug: "core",
    projectName: "Core services",
    organizationSlug: "conflict",
    sourceKind: "github",
    sourceRepo: "conflict/api-gateway",
    deployBranch: "main",
    defaultBranch: "main",
    provisioningStatus: "ready",
    provisioningError: "",
    managedHostname: "api-gateway.apps.example.com",
    isActive: true,
    isArchived: false,
    activePreviewCount: 0,
    createdAt: "2026-06-02T08:00:00Z",
    lastDeployedAt: "2026-09-28T09:40:00Z",
    healthPulse: { status: "OK", message: "latest deploy running", ageSeconds: 1200 },
    latestDeployment: {
      id: "d0000000-0000-4000-8000-000000000001",
      status: "running",
      imageTag: "sha-4f2a9c1",
      commitSha: "4f2a9c1",
      environmentName: "prod",
      triggeredBy: "leo",
      createdAt: "2026-09-28T09:38:00Z",
      startedAt: "2026-09-28T09:38:10Z",
      endedAt: "2026-09-28T09:40:00Z",
    },
    topology: "service-data" as TopologyKind,
    clusters: ["prd-us-west-2"],
    ...overrides,
  } as unknown as AppRow;
}

export const APPS: AppRow[] = [
  app({}),
  app({
    id: "a0000000-0000-4000-8000-000000000002",
    slug: "billing-worker",
    name: "Billing Worker",
    description: "",
    createdAt: "2026-07-14T08:00:00Z",
    activePreviewCount: 2,
    topology: "service-worker",
    clusters: ["prd-us-west-2", "stg-us-east-1"],
    healthPulse: { status: "DEGRADED", message: "latest deploy failed 5m ago", ageSeconds: 300 },
    latestDeployment: {
      id: "d0000000-0000-4000-8000-000000000002",
      status: "failed",
      imageTag: "sha-91be004",
      commitSha: "91be004",
      environmentName: "prod",
      triggeredBy: "ci",
      createdAt: "2026-09-28T10:02:00Z",
      startedAt: "2026-09-28T10:02:05Z",
      endedAt: "2026-09-28T10:04:00Z",
    } as AstroliftRegisteredApp["latestDeployment"],
  }),
  app({
    id: "a0000000-0000-4000-8000-000000000003",
    slug: "docs-site",
    name: "Docs Site",
    projectSlug: "web",
    projectName: "Web properties",
    teamSlug: "growth",
    provisioningStatus: "provisioning",
    managedHostname: "",
    createdAt: "2026-08-01T08:00:00Z",
    activePreviewCount: 1,
    topology: "service",
    clusters: ["stg-us-east-1"],
    lastDeployedAt: "2026-08-30T12:00:00Z",
    healthPulse: { status: "STALE", message: "no deploy in 29 days", ageSeconds: 2505600 },
  }),
  app({
    id: "a0000000-0000-4000-8000-000000000004",
    slug: "scratch",
    name: "Scratch",
    sourceRepo: "",
    sourceKind: "git_url",
    provisioningStatus: "failed",
    provisioningError: "namespace quota exceeded",
    managedHostname: "",
    isActive: false,
    createdAt: "2026-09-20T08:00:00Z",
    topology: null,
    clusters: [],
    lastDeployedAt: null,
    latestDeployment: null,
    healthPulse: { status: "NEVER", message: "no deploys yet", ageSeconds: null },
  }),
  app({
    id: "a0000000-0000-4000-8000-000000000005",
    slug: "nightly-reports",
    name: "Nightly Reports",
    description: "Scheduled exports for finance.",
    projectSlug: "web",
    projectName: "Web properties",
    teamSlug: "growth",
    isArchived: true,
    managedHostname: "",
    createdAt: "2026-03-10T08:00:00Z",
    topology: "scheduled",
    clusters: ["prd-us-west-2"],
    healthPulse: { status: "STALE", message: "archived", ageSeconds: 9000000 },
  }),
];

/** A 64-char SHA for the image tag, a 200-char ARN in the description, an unbroken repo URL. */
export const LONG_APP = app({
  id: "a0000000-0000-4000-8000-000000000099",
  slug: LONG.slice(0, 40),
  name: `${LONG} ${LONG}`,
  description: `${ARN200} ${LONG}`,
  teamSlug: LONG.slice(0, 30),
  projectSlug: LONG.slice(0, 30),
  sourceRepo: UNBROKEN_URL,
  deployBranch: `release/${LONG}`,
  activePreviewCount: 12,
  topology: "microservices",
  clusters: [LONG, "prd-us-west-2", "stg-us-east-1"],
  latestDeployment: {
    id: "d0000000-0000-4000-8000-000000000099",
    status: "running",
    imageTag: SHA64,
    commitSha: SHA64,
    environmentName: "prod",
    triggeredBy: "ci",
    createdAt: "2026-09-28T09:38:00Z",
    startedAt: "2026-09-28T09:38:10Z",
    endedAt: "2026-09-28T09:40:00Z",
  } as AstroliftRegisteredApp["latestDeployment"],
});

/** Sixty apps, for numbered pages. */
export const MANY: AppRow[] = Array.from({ length: 60 }, (_, i) => {
  const base = APPS[i % 4];
  const n = String(i + 1).padStart(2, "0");
  return {
    ...base,
    id: `a0000000-0000-4000-8000-0000000001${n}`,
    slug: `${base.slug}-${n}`,
    name: `${base.name} ${n}`,
    createdAt: `2026-09-${String((i % 27) + 1).padStart(2, "0")}T08:00:00Z`,
  };
});

export function listProps(
  patch: Partial<Omit<AppsListScreenProps, "list">> = {}
): Omit<AppsListScreenProps, "list"> {
  return {
    rows: [],
    totalCount: 0,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    canDeploy: true,
    pinned: new Set(["billing-worker"]),
    togglePin: noop,
    bulkBusy: false,
    onRollingRestart: asyncTrue,
    onPushSecrets: asyncTrue,
    onResyncManifest: asyncTrue,
    ...patch,
  };
}

export const PUSH_SECRETS: PushSecretsDialogProps = {
  open: true,
  onOpenChange: noop,
  appCount: 2,
  busy: false,
  onSubmit: async () => {},
};

export const FRESHNESS: Record<"ok" | "degraded" | "stale" | "never", AppFreshnessRowProps> = {
  ok: {
    pulse: APPS[0].healthPulse,
    latestDeployment: APPS[0].latestDeployment,
    lastDeployedAt: APPS[0].lastDeployedAt ?? null,
  },
  degraded: {
    pulse: APPS[1].healthPulse,
    latestDeployment: APPS[1].latestDeployment,
    lastDeployedAt: APPS[1].lastDeployedAt ?? null,
  },
  stale: {
    pulse: APPS[2].healthPulse,
    latestDeployment: APPS[2].latestDeployment,
    lastDeployedAt: APPS[2].lastDeployedAt ?? null,
  },
  never: { pulse: APPS[3].healthPulse, latestDeployment: null, lastDeployedAt: null },
};
