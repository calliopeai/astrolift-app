import type {
  AstroliftDeployment,
  AstroliftDeploymentApprovalHistoryEntry,
} from "@/graphql/lifecycle/lifecycle.types";

import type { ApprovalHistoryPanelProps } from "./ApprovalHistory";
import type { ApprovalScreenProps } from "./ApprovalScreen";
import type { ApprovalsQueueScreenProps } from "./ApprovalsQueue";

/** Hand-typed fixtures for the approval page and the approvals queue. */

const noop = async () => {};

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

export const DEPLOYMENT: AstroliftDeployment = {
  version: 1,
  id: "5f1c2a4e-8d7b-4a61-9a3e-0c2f7b9d1e44",
  status: "pending_approval",
  statusReason: "",
  triggerKind: "push",
  strategy: "rolling",
  registeredAppSlug: "checkout-api",
  environmentName: "production",
  workloadSlug: "checkout-api-web",
  imageTag: "v2.14.0-3f9a1c2",
  imageDigest: "sha256:9b1d4c0e7a2f58d63b2e1f0a4c7d9e8b6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d",
  clusterRevision: "rev-418",
  branch: "main",
  commitSha: "3f9a1c2b7d4e8f0a1b2c3d4e5f6a7b8c9d0e1f2a",
  commitAuthor: "Maya Chen",
  commitAuthorAvatarUrl: "",
  commitMessage:
    "Retry payment capture on gateway 503\n\nThe gateway returns 503 during its nightly failover.\nWe now retry capture up to three times with jitter.\nIdempotency keys make the retry safe.",
  repoUrl: "https://github.com/example/checkout-api.git",
  prNumber: 812,
  prUrl: "https://github.com/example/checkout-api/pull/812",
  ciProvider: "github",
  ciRunUrl: "",
  ciActorKind: "user",
  buildError: "",
  abortedReason: "",
  manifestResyncStatus: "",
  manifestResyncError: "",
  approvalsReceived: 1,
  approvalsRequired: 2,
  requiredApproverCount: 2,
  approvedBy: [
    {
      userId: "u-1",
      displayName: "Sam Ortiz",
      email: "sam@example.com",
      mailtoUrl: "",
      approvedAt: "2026-09-28T09:12:00Z",
    },
  ],
  awaitingApprovers: [
    {
      userId: "u-2",
      displayName: "Priya Nair",
      email: "priya@example.com",
      mailtoUrl: "mailto:priya@example.com?subject=Approve%20checkout-api",
      approvedAt: null,
    },
  ],
  triggeredByMe: false,
  triggeredByUserId: "u-3",
  createdAt: "2026-09-28T09:00:00Z",
  startedAt: null,
  endedAt: null,
  failedAt: null,
  succeededAt: null,
  durationSeconds: null,
};

export const APPROVAL: ApprovalScreenProps = {
  deployment: DEPLOYMENT,
  loading: false,
  canApprovePermission: true,
  busy: false,
  onApprove: noop,
  onReject: noop,
};

export const HISTORY_ENTRIES: AstroliftDeploymentApprovalHistoryEntry[] = [
  {
    id: "h-1",
    action: "deployment.start",
    decision: "",
    actorKind: "user",
    actorId: "u-3",
    actorDisplay: "Maya Chen",
    occurredAt: "2026-09-28T09:00:00Z",
    reason: "",
  },
  {
    id: "h-2",
    action: "deployment.approve",
    decision: "ALLOW",
    actorKind: "user",
    actorId: "u-1",
    actorDisplay: "Sam Ortiz",
    occurredAt: "2026-09-28T09:12:00Z",
    reason: "Checked the retry path against staging.",
  },
  {
    id: "h-3",
    action: "deployment.reject_by_token",
    decision: "DENY",
    actorKind: "token",
    actorId: "t-9",
    actorDisplay: "",
    occurredAt: "2026-09-28T09:20:00Z",
    reason: "Change freeze until the incident review closes.",
  },
];

export const HISTORY: ApprovalHistoryPanelProps = { entries: HISTORY_ENTRIES, loading: false };

const QUEUE_ROWS: AstroliftDeployment[] = [
  DEPLOYMENT,
  {
    ...DEPLOYMENT,
    id: "7a2b3c4d-1e2f-4a5b-8c9d-0e1f2a3b4c5d",
    registeredAppSlug: "checkout-api",
    environmentName: "production",
    imageTag: "v2.14.1-8c0d2e1",
    triggerKind: "manual",
    approvalsReceived: 0,
    createdAt: "2026-09-28T10:30:00Z",
  },
  {
    ...DEPLOYMENT,
    id: "9c8b7a6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d",
    registeredAppSlug: "ledger-worker",
    environmentName: "staging",
    imageTag: "",
    triggerKind: "ci",
    approvalsReceived: 0,
    approvalsRequired: 1,
    requiredApproverCount: 1,
    createdAt: "2026-09-27T18:45:00Z",
  },
];

export const QUEUE: ApprovalsQueueScreenProps = {
  pending: QUEUE_ROWS,
  loading: false,
  canApprove: true,
  approving: false,
  rejecting: false,
  onBulkApprove: noop,
  onBulkReject: noop,
};
