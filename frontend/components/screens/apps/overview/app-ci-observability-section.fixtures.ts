import type { AstroliftCiWorkflowSyncStatus } from "@/graphql/__generated__/schema";

import type { CiSetupSectionViewProps } from "./CiSetupSection";
import type { ObservabilitySectionViewProps } from "./ObservabilitySection";
import type { SparkPoint } from "./use-observability-summary";

/** Hand-typed fixtures for the CI setup and observability sections. */

const noop = async () => {};

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

// ---------------------------------------------------------------- CI setup

const RENDERED = `name: astrolift deploy
on:
  push:
    branches: [main]
jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-region: "us-west-2"
`;

export const SYNC_IN_SYNC: AstroliftCiWorkflowSyncStatus = {
  state: "in_sync",
  path: ".github/workflows/astrolift-ci.yml",
  detail: "",
  prUrl: "",
  currentTemplateVersion: 8,
  syncedTemplateVersion: 8,
  checkedAt: "2026-09-28T11:40:00Z",
  syncedAt: "2026-09-27T09:00:00Z",
  repoText: RENDERED,
  renderedText: RENDERED,
  repoTextPulledAt: null,
};

/** Hand-edited in the repo and the template moved on: both changed. */
export const SYNC_CONFLICT: AstroliftCiWorkflowSyncStatus = {
  ...SYNC_IN_SYNC,
  state: "conflict",
  syncedTemplateVersion: 5,
  repoText: RENDERED.replace("ubuntu-latest", "self-hosted") + "      - run: make lint\n",
  repoTextPulledAt: "2026-09-26T16:20:00Z",
};

export const SYNC_UNKNOWN: AstroliftCiWorkflowSyncStatus = {
  ...SYNC_IN_SYNC,
  state: "unknown",
  checkedAt: null,
  repoText: "",
};

const CI_DATA = {
  apiUrl: "https://api.astrolift.example.com",
  apiUrlLoading: false,
  workflowSync: {
    pushing: false,
    pulling: false,
    refreshing: false,
    reconciling: false,
    onRefresh: noop,
    onPush: noop,
    onReconcile: noop,
    onPull: noop,
  },
  pushAndRotate: { pushing: false, onPushAndRotate: noop },
  validate: {
    validating: false,
    results: {
      repo: "acme/checkout",
      results: [
        {
          secretName: "ASTROLIFT_PUSH_ROLE_ARN",
          isSet: true,
          isCurrent: true,
          updatedAt: "2026-09-20T10:00:00Z",
        },
        {
          secretName: "ASTROLIFT_ECR_URI",
          isSet: true,
          isCurrent: true,
          updatedAt: "2026-09-20T10:00:00Z",
        },
        {
          secretName: "ASTROLIFT_APP_SLUG",
          isSet: true,
          isCurrent: true,
          updatedAt: "2026-09-20T10:00:00Z",
        },
        {
          secretName: "ASTROLIFT_API_URL",
          isSet: true,
          isCurrent: true,
          updatedAt: "2026-09-20T10:00:00Z",
        },
        {
          secretName: "ASTROLIFT_DEPLOY_TOKEN",
          isSet: true,
          isCurrent: true,
          updatedAt: "2026-09-20T10:00:00Z",
        },
      ],
    },
    onValidate: noop,
  },
  syncFile: { workflowPath: ".github/workflows/astrolift-ci.yml", syncing: false, onSync: noop },
  webhook: { installing: false, onInstall: noop },
} satisfies Omit<
  CiSetupSectionViewProps,
  | "appSlug"
  | "registryUri"
  | "pushCredentialRef"
  | "providerPluginSlug"
  | "sourceWebhookInstalledAt"
  | "ciWorkflowSyncStatus"
  | "tokensHref"
>;

export const CI_SETUP: CiSetupSectionViewProps = {
  ...CI_DATA,
  appSlug: "checkout",
  registryUri: "123456789012.dkr.ecr.us-west-2.amazonaws.com/astrolift/checkout",
  pushCredentialRef: "arn:aws:iam::123456789012:role/astrolift-push-checkout",
  providerPluginSlug: "aws",
  sourceWebhookInstalledAt: "2026-09-28T09:15:00Z",
  ciWorkflowSyncStatus: SYNC_IN_SYNC,
  tokensHref: "/apps/checkout/tokens",
};

/** Fresh app: nothing provisioned, never synced, webhook not installed. */
export const CI_SETUP_EMPTY: CiSetupSectionViewProps = {
  ...CI_SETUP,
  registryUri: "",
  pushCredentialRef: "",
  providerPluginSlug: "",
  sourceWebhookInstalledAt: null,
  ciWorkflowSyncStatus: null,
  validate: { ...CI_DATA.validate, results: null },
};

export const CI_SETUP_EU: CiSetupSectionViewProps = {
  ...CI_SETUP,
  deployBranch: "release/eu",
  registryUri: CI_SETUP.registryUri.replace("us-west-2", "eu-west-1"),
  ciWorkflowSyncStatus: {
    ...SYNC_IN_SYNC,
    renderedText: RENDERED.replace("us-west-2", "eu-west-1").replace("[main]", '["release/eu"]'),
  },
};

export const CI_SETUP_APAC: CiSetupSectionViewProps = {
  ...CI_SETUP_EU,
  registryUri: CI_SETUP_EU.registryUri.replace("eu-west-1", "ap-southeast-2"),
  ciWorkflowSyncStatus: {
    ...SYNC_IN_SYNC,
    renderedText: CI_SETUP_EU.ciWorkflowSyncStatus!.renderedText.replace(
      "eu-west-1",
      "ap-southeast-2"
    ),
  },
};

export const CI_SETUP_UNAVAILABLE: CiSetupSectionViewProps = {
  ...CI_SETUP,
  ciWorkflowSyncStatus: { ...SYNC_IN_SYNC, renderedText: "" },
};

/** Platform API URL still loading, actions in flight. */
export const CI_SETUP_LOADING: CiSetupSectionViewProps = {
  ...CI_SETUP,
  apiUrl: "",
  apiUrlLoading: true,
  validate: { ...CI_DATA.validate, validating: true, results: null },
  syncFile: { ...CI_DATA.syncFile, syncing: true },
};

/** Drift conflict and secrets GitHub does not see: the closest error state. */
export const CI_SETUP_PROBLEMS: CiSetupSectionViewProps = {
  ...CI_SETUP,
  providerPluginSlug: "gcp",
  registryUri: "us-docker.pkg.dev/acme-prod/astrolift/checkout",
  pushCredentialRef:
    "projects/123456789012/locations/global/workloadIdentityPools/astrolift/providers/github",
  ciWorkflowSyncStatus: SYNC_CONFLICT,
  validate: {
    ...CI_DATA.validate,
    results: {
      repo: "acme/checkout",
      results: [
        {
          secretName: "ASTROLIFT_WORKLOAD_IDENTITY_PROVIDER",
          isSet: true,
          isCurrent: true,
          updatedAt: "2026-09-20T10:00:00Z",
        },
        {
          secretName: "ASTROLIFT_ARTIFACT_REGISTRY",
          isSet: true,
          isCurrent: false,
          updatedAt: "2026-08-01T10:00:00Z",
        },
        { secretName: "ASTROLIFT_APP_SLUG", isSet: false, isCurrent: false, updatedAt: null },
        { secretName: "ASTROLIFT_API_URL", isSet: false, isCurrent: false, updatedAt: null },
        {
          secretName: "ASTROLIFT_DEPLOY_TOKEN",
          isSet: true,
          isCurrent: false,
          updatedAt: "2026-07-14T10:00:00Z",
        },
      ],
    },
  },
};

export const CI_SETUP_LONG: CiSetupSectionViewProps = {
  ...CI_SETUP,
  appSlug: LONG,
  providerPluginSlug: "azure",
  registryUri: `${LONG}.azurecr.io`,
  pushCredentialRef: `00000000-0000-4000-8000-000000000000-${LONG}`,
  apiUrl: `https://${LONG}.astrolift.example.com`,
  ciWorkflowSyncStatus: { ...SYNC_UNKNOWN, repoText: `# ${LONG}\n${RENDERED}` },
};

/** Agent package: source delivery, no image build contract. */
export const CI_SETUP_AGENT: CiSetupSectionViewProps = {
  ...CI_SETUP,
  agentMode: true,
  appSlug: "bdr-agent",
  syncFile: {
    ...CI_DATA.syncFile,
    workflowPath: ".github/workflows/astrolift-agent-bdr-agent.yml",
  },
};

// ----------------------------------------------------------- Observability

const series = (values: number[]): SparkPoint[] =>
  values.map((value, i) => ({
    ts: new Date(Date.UTC(2026, 8, 15 + i)).toISOString(),
    value,
  }));

const ZEROES = Array.from({ length: 14 }, () => 0);

export const OBSERVABILITY: ObservabilitySectionViewProps = {
  appSlug: "checkout",
  days: 14,
  error: null,
  onRetry: () => {},
  deploysLoading: false,
  deploysSeries: series([2, 1, 0, 3, 4, 2, 0, 0, 5, 3, 2, 1, 4, 3]),
  errorSeries: series([0, 0, 0, 1, 0, 0, 0, 0, 2, 0, 0, 0, 1, 0]),
  totalDeploys: 30,
  failedCount: 4,
  unresolvedCount: 3,
  criticalCount: 0,
  alertsLoading: false,
};

export const OBSERVABILITY_LOADING: ObservabilitySectionViewProps = {
  ...OBSERVABILITY,
  deploysLoading: true,
  deploysSeries: series(ZEROES),
  errorSeries: series(ZEROES),
  totalDeploys: 0,
  failedCount: 0,
  alertsLoading: true,
};

export const OBSERVABILITY_EMPTY: ObservabilitySectionViewProps = {
  ...OBSERVABILITY_LOADING,
  deploysLoading: false,
  alertsLoading: false,
  unresolvedCount: 0,
};

/** Failing rollouts and critical alerts: the closest error state. */
export const OBSERVABILITY_FAILING: ObservabilitySectionViewProps = {
  ...OBSERVABILITY,
  errorSeries: series([1, 1, 0, 3, 2, 2, 0, 0, 4, 3, 2, 1, 4, 3]),
  failedCount: 26,
  unresolvedCount: 12,
  criticalCount: 5,
};

export const OBSERVABILITY_LONG: ObservabilitySectionViewProps = {
  ...OBSERVABILITY,
  appSlug: LONG,
  deploysSeries: series([120, 98, 143, 110, 131, 150, 99, 101, 160, 149, 132, 118, 170, 155]),
  totalDeploys: 1836,
  failedCount: 247,
  unresolvedCount: 1204,
  criticalCount: 318,
};
