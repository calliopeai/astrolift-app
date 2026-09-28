import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { AppDetailScreenProps } from "./AppDetail";
import type { AppTabsViewProps } from "./AppTabs";
import type { DeployActivityStripProps } from "./DeployActivityStrip";
import type { QuickLinksGridProps } from "./QuickLinksGrid";
import type { RepoBadgeProps } from "./RepoBadge";

/**
 * Hand-typed fixtures for the app detail shell: the overview screen, its tab
 * bar, and the header/overview pieces this group owns. The app and deployment
 * records carry only the fields these views read.
 */

export const LONG =
  "platform-team-shared-production-checkout-service-with-a-deliberately-long-name-that-keeps-going";

export const APP_HREF = "/apps/checkout";

export const APP = {
  id: "app-1",
  slug: "checkout",
  name: "Checkout",
  sourceKind: "github",
  sourceUrl: "https://github.com/acme/checkout",
  sourceRepo: "acme/checkout",
  deployBranch: "main",
  defaultBranch: "main",
  provisioningStatus: "ready",
  provisioningError: "",
  managedHostname: "checkout.astrolift.example.com",
  subdomain: "checkout",
  previewScreenshotUrl: "",
  manifestBootstrapStatus: "ok",
  manifestBootstrapError: "",
  configDrift: null,
} as AstroliftRegisteredApp;

export const WORKLOADS = [
  { id: "wl-1", slug: "web", name: "web", kind: "deployment", isPublic: true },
] as AstroliftWorkload[];

function deployment(
  id: string,
  status: DeploymentStatus,
  minutesAgo: number,
  over: Partial<AstroliftDeployment> = {}
): AstroliftDeployment {
  return {
    id,
    status,
    imageTag: `sha-${id.padEnd(10, "0")}`,
    environmentName: "production",
    commitSha: "9f3c2a1b7e4d",
    commitAuthor: "leo",
    ciActorKind: "",
    triggerKind: "push",
    createdAt: new Date(Date.UTC(2026, 8, 28, 12, 0) - minutesAgo * 60_000).toISOString(),
    ...over,
  } as AstroliftDeployment;
}

/** Newest first, as LIST_DEPLOYMENTS returns them. */
export const DEPLOYMENTS: AstroliftDeployment[] = [
  deployment("d12", "deploying", 2),
  deployment("d11", "pending_approval", 20),
  deployment("d10", "running", 60),
  deployment("d09", "superseded", 180),
  deployment("d08", "failed", 240),
  deployment("d07", "rolled_back", 300),
  deployment("d06", "superseded", 600),
  deployment("d05", "superseded", 900),
  deployment("d04", "failed", 1400),
  deployment("d03", "superseded", 2000),
  deployment("d02", "superseded", 3000),
  deployment("d01", "superseded", 4000, { environmentName: "staging" }),
];

// ─── AppTabs ──────────────────────────────────────────────────────────────────

export const TABS: AppTabsViewProps = {
  slug: "checkout",
  basePath: "/apps",
  pathname: "/apps/checkout",
};

// ─── RepoBadge ────────────────────────────────────────────────────────────────

export const REPO_BADGE: RepoBadgeProps = {
  sourceKind: "github",
  sourceUrl: "https://github.com/acme/checkout",
  sourceRepo: "acme/checkout",
  branch: "main",
};

// ─── DeployActivityStrip ──────────────────────────────────────────────────────

export const ACTIVITY: DeployActivityStripProps = {
  appHref: APP_HREF,
  deployments: DEPLOYMENTS,
  loading: false,
  limit: 20,
};

// ─── QuickLinksGrid ───────────────────────────────────────────────────────────

export const QUICK_LINKS: QuickLinksGridProps = {
  appHref: APP_HREF,
  count: 42,
  loading: false,
};

// ─── AppDetailScreen ──────────────────────────────────────────────────────────

export const DETAIL: AppDetailScreenProps = {
  slug: "checkout",
  loading: false,
  app: APP,
  workloads: WORKLOADS,
};

export const LONG_APP = {
  ...APP,
  slug: LONG,
  name: LONG,
  sourceRepo: `acme/${LONG}`,
  sourceUrl: `https://github.com/acme/${LONG}`,
  deployBranch: `feature/${LONG}`,
  managedHostname: `${LONG}.astrolift.example.com`,
} as AstroliftRegisteredApp;

export const FAILED_APP = {
  ...APP,
  provisioningStatus: "failed",
  managedHostname: "",
  provisioningError:
    "namespace checkout: create ServiceAccount astrolift-deployer: admission webhook denied the request",
} as AstroliftRegisteredApp;
