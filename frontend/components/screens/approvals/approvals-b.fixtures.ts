import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";
import { defaultListState, type ListStateController } from "@/components/list/list-state";
import { SECRET_PROPOSALS_LIST } from "./secret-proposals-list";
import type { SecretProposalMetadata } from "./use-secret-proposals-queue";
import type { PendingHumanGate } from "@/graphql/workflows/tiered.types";

import type { PendingGatesScreenProps } from "./PendingGates";
import type { SecretProposalDetailScreenProps } from "./SecretProposalDetail";
import type { SecretProposalsQueueProps } from "./SecretProposalsQueue";
import type { SecretProposalsSummaryProps } from "./SecretProposalsSummary";

/** Hand-typed fixtures for the secret-proposal queue, its detail page, and pending gates. */

/** The generated JSON scalar is typed as an object; real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;
const noop = async () => {};

export const LONG_B =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

export const PROPOSAL: AstroliftSecretChangeProposal = {
  id: "prop-1",
  registeredAppSlug: "billing-api",
  environmentName: "prod",
  op: "set",
  status: "pending",
  proposerUserId: "user-1",
  proposerDisplayName: "Dana Ortiz",
  requiredApproverCount: 2,
  approvalsCount: 1,
  expiresAt: "2026-09-30T14:00:00Z",
  decidedAt: null,
  appliedAt: null,
  applyError: "",
  createdAt: "2026-09-28T09:30:00Z",
  payload: json({ key: "DATABASE_URL" }),
  payloadDiff: json({
    op: "set",
    summary: "Rotate DATABASE_URL",
    before: { DATABASE_URL: "postgres://•••••" },
    after: { DATABASE_URL: "postgres://•••••" },
  }),
  approvals: [
    {
      id: "appr-1",
      approverUserId: "user-2",
      approverDisplayName: "Sam Lee",
      decision: "approved",
      decidedAt: "2026-09-28T10:02:00Z",
      reason: "",
    },
  ],
};

export const PROPOSALS: AstroliftSecretChangeProposal[] = [
  PROPOSAL,
  {
    ...PROPOSAL,
    id: "prop-2",
    registeredAppSlug: "web-frontend",
    environmentName: "",
    op: "delete",
    proposerDisplayName: "",
    approvalsCount: 0,
    requiredApproverCount: 1,
    payloadDiff: json({ before: { LEGACY_TOKEN: "•••••" }, after: {} }),
    approvals: [],
  },
];

export const LONG_PROPOSAL: AstroliftSecretChangeProposal = {
  ...PROPOSAL,
  id: "prop-long",
  registeredAppSlug: LONG_B,
  environmentName: `${LONG_B}-env`,
  proposerDisplayName: `${LONG_B}@example.com`,
  applyError: `secret store write failed: ${LONG_B} ${LONG_B}`,
  payloadDiff: json({
    summary: `Rotate ${LONG_B}`,
    before: { [`${LONG_B}_KEY`]: `${LONG_B}${LONG_B}` },
    after: { [`${LONG_B}_KEY`]: `${LONG_B}${LONG_B}` },
  }),
  approvals: [
    {
      id: "appr-long",
      approverUserId: "user-3",
      approverDisplayName: `${LONG_B}@example.com`,
      decision: "rejected",
      decidedAt: "2026-09-28T10:02:00Z",
      reason: `Not during the freeze. ${LONG_B}`,
    },
  ],
};

export function proposalMetadata(proposal: AstroliftSecretChangeProposal): SecretProposalMetadata {
  return {
    id: proposal.id,
    registeredAppSlug: proposal.registeredAppSlug,
    environmentName: proposal.environmentName,
    op: proposal.op,
    status: proposal.status,
    proposerDisplayName: proposal.proposerDisplayName,
    requiredApproverCount: proposal.requiredApproverCount,
    approvalsCount: proposal.approvalsCount,
    expiresAt: proposal.expiresAt,
    decidedAt: proposal.decidedAt,
    appliedAt: proposal.appliedAt,
    createdAt: proposal.createdAt,
  };
}

const list: ListStateController = {
  definition: SECRET_PROPOSALS_LIST,
  state: defaultListState(SECRET_PROPOSALS_LIST),
  filters: {},
  isFiltered: false,
  setSearch: noop,
  setFilter: noop,
  applySearch: noop,
  clearFilters: noop,
  toggleSort: noop,
  setSort: noop,
  setPage: noop,
  older: noop,
  newer: noop,
  hasNewer: false,
  setPageSize: noop,
  viewHref: () => "/approvals/secret",
  hiddenColumns: [],
  toggleColumn: noop,
  mode: "list",
  setMode: noop,
};
export const QUEUE: SecretProposalsQueueProps = {
  list,
  rows: PROPOSALS.map(proposalMetadata),
  totalCount: 2,
  nextCursor: null,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  onRefresh: noop,
};
export const SECRET_SUMMARY: SecretProposalsSummaryProps = {
  rows: PROPOSALS.map(proposalMetadata),
  count: 2,
  loading: false,
  error: null,
  onRetry: noop,
};

export const DETAIL: SecretProposalDetailScreenProps = {
  error: null,
  onRetry: () => {},
  proposal: PROPOSAL,
  loading: false,
  canApprove: true,
  approving: false,
  rejecting: false,
  withdrawing: false,
  onApprove: noop,
  onReject: noop,
  onWithdraw: noop,
};

export const GATE: PendingHumanGate = {
  executionId: "72",
  runGuid: "run-guid-1",
  workflowId: "WorkflowDefinitionRunWorkflow-320",
  definitionSlug: "outreach-review",
  definitionName: "Outreach Review",
  stageRole: "outreach review",
  stageApprovers: ["team:gtm"],
  startedAt: "2026-09-28T08:00:00Z",
};

export const GATES: PendingHumanGate[] = [
  GATE,
  {
    ...GATE,
    executionId: "73",
    runGuid: "run-guid-2",
    definitionSlug: "release-signoff",
    definitionName: "",
    stageRole: "",
    stageApprovers: ["team:platform", "user:leo"],
    startedAt: null,
  },
];

export const LONG_GATE: PendingHumanGate = {
  ...GATE,
  executionId: "74",
  runGuid: "run-guid-long",
  definitionSlug: LONG_B,
  definitionName: `${LONG_B} review`,
  stageRole: `${LONG_B} role`,
  stageApprovers: [`team:${LONG_B}`, `user:${LONG_B}@example.com`],
};

export const PENDING_GATES: PendingGatesScreenProps = {
  gates: GATES,
  loading: false,
  error: null,
  onRefresh: () => {},
};
