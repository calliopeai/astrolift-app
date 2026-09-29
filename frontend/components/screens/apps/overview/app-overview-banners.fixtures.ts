import type {
  AstroliftAppAutowireStatus,
  AstroliftAppConfigDrift,
  AstroliftAppReprovisionState,
  ProvisioningProgress,
} from "@/graphql/registry/registry.types";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import type { AutowireStatusBannerViewProps } from "./AutowireStatusBanner";
import type { ConfigDriftBannerViewProps } from "./ConfigDriftBanner";
import type { DeregisterPendingBannerViewProps } from "./DeregisterPendingBanner";
import type { GithubConnectCalloutViewProps } from "./GithubConnectCallout";
import type { ProvisioningProgressViewProps } from "./ProvisioningProgress";
import type { ReprovisionCalloutViewProps } from "./ReprovisionCallout";

/** Hand-typed fixtures for the app overview banners and callouts. */

const noop = async () => {};

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

export const APP_SLUG = "checkout";

// Autowire (#1108)
export const AUTOWIRE_FAILING: AstroliftAppAutowireStatus = {
  connected: true,
  ciWorkflow: "ok",
  webhook: "phantom",
  secrets: "error",
  checkedAt: "2026-09-28T12:00:00Z",
  detail:
    "webhook 41223 marked installed but no delivery in 24h; secret push: 403 Resource not accessible",
};

export const AUTOWIRE_NOT_CONNECTED: AstroliftAppAutowireStatus = {
  connected: false,
  ciWorkflow: "missing",
  webhook: "missing",
  secrets: "missing",
  checkedAt: null,
  detail: "",
};

export const AUTOWIRE_WIRED: AstroliftAppAutowireStatus = {
  connected: true,
  ciWorkflow: "ok",
  webhook: "ok",
  secrets: "ok",
  checkedAt: "2026-09-28T12:00:00Z",
  detail: "",
};

export const AUTOWIRE: AutowireStatusBannerViewProps = {
  sourceKind: "github",
  sourceRepo: "acme/checkout",
  autowire: AUTOWIRE_FAILING,
  retrying: false,
  onRetry: noop,
};

// Config drift (#407 C)
export const DRIFT: AstroliftAppConfigDrift = {
  environmentName: "production",
  fields: ["manifest_hash", "image_tag", "repo_unsynced"],
  hasDrift: true,
  lastChecked: "2026-09-28T12:00:00Z",
};

export const CONFIG_DRIFT: ConfigDriftBannerViewProps = {
  drift: DRIFT,
  resyncing: false,
  onResync: noop,
};

// Deregister grace window (#436 B)
export const DEREGISTER: DeregisterPendingBannerViewProps = {
  msRemaining: 272_000,
  cancelling: false,
  onCancel: noop,
};

// Personal GitHub connect
const CONNECTION: AstroliftSourceConnection = {
  accountLogin: "acme",
  apiBaseUrl: "https://api.github.com",
  appClientId: "",
  createdAt: "2026-09-01T09:00:00Z",
  displayName: "Acme GitHub OAuth app",
  id: "0b6c1d52-6f0e-4d3a-9d8c-2f1b7c9e4a10",
  installationId: "",
  isActive: true,
  isOauthAppConfig: true,
  isPersonal: false,
  kind: "github_oauth_app",
  lastUsedAt: null,
  name: "acme-oauth",
  needsClientId: false,
  oauthClientId: "Iv1.4f2a9c0e7b31d6a8",
  oauthRedirectUri: "https://astrolift.example.com/app/auth1/scm/github/callback",
  parentOauthAppId: null,
  repoVisibilityScopes: ["private_org"],
  tokenExpiresAt: null,
  updatedAt: "2026-09-01T09:00:00Z",
  userUsername: null,
};

export const OAUTH_APP_CONNECTION = CONNECTION;

export const PERSONAL_CONNECTION: AstroliftSourceConnection = {
  ...CONNECTION,
  id: "7a1e3b90-2c4d-4e5f-8a6b-9c0d1e2f3a4b",
  isOauthAppConfig: false,
  isPersonal: true,
  kind: "github_oauth_user",
  name: "leo",
  displayName: "leo (personal)",
  userUsername: "leo",
};

export const GITHUB_CONNECT: GithubConnectCalloutViewProps = {
  sourceKind: "github",
  loading: false,
  connections: [OAUTH_APP_CONNECTION],
};

// Reprovision (#407 A)
export const REPROVISION_FAILED: AstroliftAppReprovisionState = {
  needsReprovision: true,
  state: "failed",
  reason: "",
  elapsedSeconds: null,
};

export const REPROVISION_IN_FLIGHT: AstroliftAppReprovisionState = {
  needsReprovision: true,
  state: "provisioning",
  reason: "",
  elapsedSeconds: 420,
};

export const REPROVISION: ReprovisionCalloutViewProps = {
  appSlug: APP_SLUG,
  reprovision: REPROVISION_FAILED,
  redeploying: false,
  onConfirm: noop,
};

// Provisioning progress
export const PROGRESS: ProvisioningProgress = {
  currentStep: "identity",
  completed: ["registry", "namespace"],
  totalSteps: ["registry", "namespace", "identity", "managed_services", "ready"],
};

export const PROVISIONING: ProvisioningProgressViewProps = {
  isProvisioning: true,
  progress: PROGRESS,
};
