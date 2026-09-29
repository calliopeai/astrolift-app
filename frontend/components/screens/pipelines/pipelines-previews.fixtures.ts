/**
 * Hand-typed story fixtures for the pipelines and previews screens, typed
 * against each view's props so a story cannot drift from its hook.
 */
import type { PipelineSecret } from "@/graphql/pipelines/pipelines.types";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { PipelineDetailScreenProps, RunGraphViewProps } from "./PipelineDetail";
import type { PipelineSecretsViewProps } from "./PipelineSecrets";
import type { PipelineListViewProps, RunHistoryViewProps } from "./PipelinesScreen";
import type { Pipeline, PipelineRunRow } from "./use-pipelines";
import type { PipelineDetailRun } from "./use-pipeline-detail";

const noop = () => {};
const resolveVoid = async () => {};
const resolveTrue = async () => true;

const LONG_NAME =
  "customer-facing-checkout-and-billing-reconciliation-service-with-an-unreasonably-long-name";

// ---------------------------------------------------------------------------
// Pipelines list
// ---------------------------------------------------------------------------

export const PIPELINES: Pipeline[] = [
  {
    id: "p-1f0c",
    name: "checkout-api",
    repoUrl: "https://github.com/acme/checkout-api",
    defaultBranch: "main",
    tomlPath: ".astrolift/pipeline.toml",
    createdAt: "2026-08-14T10:00:00Z",
  },
  {
    id: "p-2a7d",
    name: "web-frontend",
    repoUrl: "https://github.com/acme/web",
    defaultBranch: "trunk",
    tomlPath: "ci/pipeline.toml",
    createdAt: "2026-09-01T08:15:00Z",
  },
  {
    id: "p-3b9e",
    name: "nightly-etl",
    repoUrl: "https://gitlab.example.com/data/etl",
    defaultBranch: "main",
    tomlPath: ".astrolift/pipeline.toml",
    createdAt: "2026-09-20T08:15:00Z",
  },
];

export const LONG_PIPELINE: Pipeline = {
  id: "p-long",
  name: LONG_NAME,
  repoUrl: `https://github.com/acme-enterprise-holdings/${LONG_NAME}`,
  defaultBranch: "release/2026-q4-hotfix-for-the-payment-gateway-timeout-regression",
  tomlPath: "services/billing/reconciliation/.astrolift/pipelines/production/pipeline.toml",
  createdAt: "2026-09-20T08:15:00Z",
};

/** The list tab's props less the list controller, which the story builds. */
export function pipelineListProps(
  extra: Partial<Omit<PipelineListViewProps, "list">> = {}
): Omit<PipelineListViewProps, "list"> {
  return {
    rows: PIPELINES,
    totalCount: PIPELINES.length,
    nextCursor: null,
    loading: false,
    error: null,
    onRetry: noop,
    onTrigger: resolveVoid,
    triggering: false,
    ...extra,
  };
}

export const RUN_ROWS: PipelineRunRow[] = [
  {
    id: "r-104",
    runNumber: 104,
    triggerKind: "push",
    triggerRef: "refs/heads/main",
    triggerActor: "ada.lovelace",
    status: "running",
    startedAt: "2026-09-28T07:40:00Z",
    finishedAt: null,
  },
  {
    id: "r-103",
    runNumber: 103,
    triggerKind: "manual",
    triggerRef: "refs/heads/main",
    triggerActor: "grace.hopper",
    status: "success",
    startedAt: "2026-09-27T16:00:00Z",
    finishedAt: "2026-09-27T16:03:12Z",
  },
  {
    id: "r-102",
    runNumber: 102,
    triggerKind: "webhook",
    triggerRef: "refs/heads/feat/retry",
    triggerActor: null,
    status: "failure",
    startedAt: "2026-09-27T12:00:00Z",
    finishedAt: "2026-09-27T12:01:05Z",
  },
  {
    id: "r-101",
    runNumber: 101,
    triggerKind: "push",
    triggerRef: "refs/heads/main",
    triggerActor: "ada.lovelace",
    status: "pending",
    startedAt: null,
    finishedAt: null,
  },
];

export const LONG_RUN: PipelineRunRow = {
  id: "r-long",
  runNumber: 98765,
  triggerKind: "webhook",
  triggerRef: "refs/heads/release/2026-q4-hotfix-for-the-payment-gateway-timeout-regression",
  triggerActor: "a-service-account-with-an-extremely-long-name@acme-enterprise.example.com",
  status: "cancelled",
  startedAt: "2026-09-27T12:00:00Z",
  finishedAt: "2026-09-27T13:15:42Z",
};

/** The run history tab's props less the list controller. */
export function runHistoryProps(
  extra: Partial<Omit<RunHistoryViewProps, "list">> = {}
): Omit<RunHistoryViewProps, "list"> {
  return {
    rows: RUN_ROWS,
    totalCount: RUN_ROWS.length,
    nextCursor: null,
    loading: false,
    error: null,
    onRetry: noop,
    options: PIPELINES,
    selected: PIPELINES[0].id,
    onSelect: noop,
    loadingOptions: false,
    ...extra,
  };
}

// ---------------------------------------------------------------------------
// Pipeline detail
// ---------------------------------------------------------------------------

export const DETAIL_RUNS: PipelineDetailRun[] = RUN_ROWS.map(
  ({ id, runNumber, status, triggerKind, triggerRef, startedAt, finishedAt }) => ({
    id,
    runNumber,
    status,
    triggerKind,
    triggerRef,
    startedAt,
    finishedAt,
  })
);

export const DETAIL: Omit<PipelineDetailScreenProps, "secrets"> = {
  tab: "runs",
  onTabChange: noop,
  runs: DETAIL_RUNS,
  runsLoading: false,
};

export const RUN_GRAPH: RunGraphViewProps = {
  loading: false,
  stages: [
    {
      id: "build",
      name: "Build",
      status: "success",
      needs: [],
      startedAt: "2026-09-28T07:40:00Z",
      finishedAt: "2026-09-28T07:41:30Z",
    },
    {
      id: "test",
      name: "Test",
      status: "success",
      needs: ["build"],
      startedAt: "2026-09-28T07:41:30Z",
      finishedAt: "2026-09-28T07:43:00Z",
    },
    {
      id: "lint",
      name: "Lint",
      status: "success",
      needs: ["build"],
      startedAt: "2026-09-28T07:41:30Z",
      finishedAt: "2026-09-28T07:42:00Z",
    },
    {
      id: "deploy",
      name: "Deploy",
      status: "running",
      needs: ["test", "lint"],
      startedAt: "2026-09-28T07:43:00Z",
      finishedAt: null,
    },
  ],
};

// ---------------------------------------------------------------------------
// Pipeline secrets
// ---------------------------------------------------------------------------

export const SECRETS: PipelineSecret[] = [
  {
    id: "s-1",
    name: "NPM_TOKEN",
    createdAt: "2026-08-14T10:00:00Z",
    updatedAt: "2026-09-02T12:30:00Z",
  },
  {
    id: "s-2",
    name: "SLACK_WEBHOOK_URL",
    createdAt: "2026-09-01T08:15:00Z",
    updatedAt: "2026-09-01T08:15:00Z",
  },
];

export const LONG_SECRET: PipelineSecret = {
  id: "s-long",
  name: "PAYMENT_GATEWAY_RECONCILIATION_SERVICE_PRODUCTION_READ_WRITE_API_KEY_ROTATED_QUARTERLY",
  createdAt: "2026-09-01T08:15:00Z",
  updatedAt: "2026-09-27T08:15:00Z",
};

export function secretsProps(
  extra: Partial<PipelineSecretsViewProps> = {}
): PipelineSecretsViewProps {
  return {
    secrets: SECRETS,
    loading: false,
    deleting: false,
    saveSecret: resolveTrue,
    deleteSecret: resolveVoid,
    ...extra,
  };
}

// ---------------------------------------------------------------------------
// Previews
// ---------------------------------------------------------------------------

export const PREVIEWS: AstroliftPreviewEnvironment[] = [
  {
    id: "9b2f4c1e-0a7d-4e3b-8c6f-1d2e3f4a5b6c",
    registeredAppSlug: "checkout-api",
    namespace: "pv-checkout-api-pr-412",
    prNumber: 412,
    prUrl: "https://github.com/acme/checkout-api/pull/412",
    branch: "feat/retry-budget",
    commitSha: "4f1c9a20b5e7d219c0a7e6f5d4c3b2a1",
    hostname: "pr-412.checkout-api.preview.acme.dev",
    sourceUrl: "https://github.com/acme/checkout-api",
    status: "running",
    openedByLogin: "barbara",
    openedByMe: false,
    failureReason: "",
    isManual: false,
    isPinned: false,
    pinReason: "",
    pinnedAt: null,
    pinnedByEmail: null,
    ttlUntil: "2026-10-05T10:00:00Z",
    lastDeployedAt: "2026-09-28T07:42:00Z",
    tornDownAt: null,
    aggregateResources: { cpuCores: 0.75, memoryBytes: 805306368, podCount: 3 },
    estimatedDailyCostUsd: 1.24,
    estimatedCostApproximate: false,
    estimatedCostNotes: [],
  },
  {
    id: "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
    registeredAppSlug: "web-frontend",
    namespace: "pv-web-frontend-pr-88",
    prNumber: 88,
    prUrl: "https://github.com/acme/web/pull/88",
    branch: "fix/nav-focus",
    commitSha: "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    hostname: "pr-88.web.preview.acme.dev",
    sourceUrl: "https://github.com/acme/web",
    status: "building",
    openedByLogin: "leo",
    openedByMe: true,
    failureReason: "",
    isManual: true,
    isPinned: false,
    pinReason: "",
    pinnedAt: null,
    pinnedByEmail: null,
    ttlUntil: "2026-10-01T10:00:00Z",
    lastDeployedAt: null,
    tornDownAt: null,
    aggregateResources: { cpuCores: 0, memoryBytes: 0, podCount: 0 },
    estimatedDailyCostUsd: null,
    estimatedCostApproximate: false,
    estimatedCostNotes: [],
  },
  {
    id: "7e8f9a0b-1c2d-4e3f-a4b5-c6d7e8f9a0b1",
    registeredAppSlug: "checkout-api",
    namespace: "pv-checkout-api-pr-398",
    prNumber: 398,
    prUrl: "",
    branch: "chore/deps",
    commitSha: "",
    hostname: "pr-398.checkout-api.preview.acme.dev",
    sourceUrl: "",
    status: "torn_down",
    openedByLogin: "dependabot[bot]",
    openedByMe: false,
    failureReason: "",
    isManual: false,
    isPinned: false,
    pinReason: "",
    pinnedAt: null,
    pinnedByEmail: null,
    ttlUntil: "2026-09-20T10:00:00Z",
    lastDeployedAt: "2026-09-18T09:00:00Z",
    tornDownAt: "2026-09-20T10:00:00Z",
    aggregateResources: { cpuCores: 0, memoryBytes: 0, podCount: 0 },
    estimatedDailyCostUsd: null,
    estimatedCostApproximate: false,
    estimatedCostNotes: [],
  },
  {
    id: "3a4b5c6d-7e8f-4a9b-8c0d-1e2f3a4b5c6d",
    registeredAppSlug: "search-indexer",
    namespace: "pv-search-indexer-pr-17",
    prNumber: 17,
    prUrl: "https://github.com/acme/search/pull/17",
    branch: "feat/gcp-variant",
    commitSha: "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
    hostname: "pr-17.search.preview.acme.dev",
    sourceUrl: "https://github.com/acme/search",
    status: "failed",
    openedByLogin: "dennis",
    openedByMe: false,
    failureReason: "Image build failed: step 7/12 exited 1 (npm ci).",
    isManual: false,
    isPinned: false,
    pinReason: "",
    pinnedAt: null,
    pinnedByEmail: null,
    ttlUntil: "2026-10-02T10:00:00Z",
    lastDeployedAt: "2026-09-27T18:30:00Z",
    tornDownAt: null,
    aggregateResources: { cpuCores: 2.5, memoryBytes: 4294967296, podCount: 6 },
    estimatedDailyCostUsd: 9.87,
    estimatedCostApproximate: true,
    estimatedCostNotes: [
      "GCP variant: node pool cost is summed per pod, so this is an over-count.",
      "Egress is not included.",
    ],
  },
];

export const LONG_PREVIEW: AstroliftPreviewEnvironment = {
  ...PREVIEWS[3],
  id: "5f6e7d8c-9b0a-4f1e-8d2c-3b4a5f6e7d8c",
  registeredAppSlug: LONG_NAME,
  namespace: `pv-${LONG_NAME}-pr-123456`,
  prNumber: 123456,
  branch: "release/2026-q4-hotfix-for-the-payment-gateway-timeout-regression-and-retry-storm",
  hostname: `pr-123456.${LONG_NAME}.preview.acme-enterprise-holdings.example.com`,
  status: "running",
  estimatedCostNotes: [
    "GCP variant: node pool cost is summed per pod rather than per node, so the total here is an over-count by construction and may be out by an order of magnitude on dense pools.",
  ],
};
