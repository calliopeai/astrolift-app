/**
 * Services types — facade over the codegen output (#401).
 *
 * The schema carries every field; this file narrows the shapes our
 * UI consumes so components don't import the giant generated module
 * directly.
 */

import type {
  AstroliftManagedService as GeneratedManagedService,
  AstroliftManagedServiceConnection as GeneratedManagedServiceConnection,
  AstroliftManagedServiceConnectionKey as GeneratedManagedServiceConnectionKey,
  AstroliftManagedServiceObject as GeneratedManagedServiceObject,
  AstroliftManagedServiceObjects as GeneratedManagedServiceObjects,
  AstroliftManagedServiceQueueDepth as GeneratedManagedServiceQueueDepth,
  AstroliftManagedServiceTestEmailResult as GeneratedManagedServiceTestEmailResult,
  AstroliftSecretChangeApproval as GeneratedSecretChangeApproval,
  AstroliftSecretChangeProposal as GeneratedSecretChangeProposal,
} from "@/graphql/__generated__/schema";

export type AstroliftManagedService = Pick<
  GeneratedManagedService,
  | "id"
  | "name"
  | "kind"
  | "variant"
  | "status"
  | "statusError"
  | "environmentName"
  | "registeredAppSlug"
  | "createdAt"
  | "updatedAt"
  | "lastActionAt"
  | "lastActionKind"
  | "editableFields"
> & {
  /** JSON scalar — opaque shape; callers cast as needed. */
  config: Record<string, unknown>;
};

export type AstroliftManagedServiceConnectionKey = GeneratedManagedServiceConnectionKey;

export type AstroliftManagedServiceConnection = GeneratedManagedServiceConnection;

export type AstroliftManagedServiceObject = GeneratedManagedServiceObject;

export type AstroliftManagedServiceObjects = GeneratedManagedServiceObjects;

export type AstroliftManagedServiceQueueDepth = GeneratedManagedServiceQueueDepth;

export type AstroliftManagedServiceTestEmailResult = GeneratedManagedServiceTestEmailResult;

// Email observability surface (#629, #631, #632, #633, #634) ---------
//
// Handcrafted shapes; the codegen output will pick these up once the
// schema regen runs, but the UI doesn't need to wait for that — the
// fields are stable and the GraphQL contract owns the wire format.

export interface AstroliftEmailSendQuota {
  maxSendRate: number;
  max24HourSend: number;
  sentLast24h: number;
}

export interface AstroliftEmailAccountStatus {
  sendingEnabled: boolean;
  productionAccess: boolean;
  reputationScore: number | null;
  bounceRatePct: number | null;
  complaintRatePct: number | null;
}

export interface AstroliftEmailDkimToken {
  token: string;
  cnameHost: string;
  cnameTarget: string;
}

export interface AstroliftEmailIdentityVerification {
  identity: string;
  isDomain: boolean;
  status: string;
  verificationToken: string;
  dkimTokens: AstroliftEmailDkimToken[];
}

export type EmailDnsCheckOutcome = "GREEN" | "YELLOW" | "RED" | "UNKNOWN";

export interface AstroliftEmailDnsAuthCheck {
  protocol: string;
  outcome: EmailDnsCheckOutcome;
  records: string[];
  message: string;
}

export interface AstroliftEmailDnsAuthStatus {
  identity: string;
  checkedAt: string;
  overall: EmailDnsCheckOutcome;
  dkim: AstroliftEmailDnsAuthCheck;
  spf: AstroliftEmailDnsAuthCheck;
  dmarc: AstroliftEmailDnsAuthCheck;
}

export type EmailSuppressionReason = "BOUNCE" | "COMPLAINT" | "MANUAL";

export interface AstroliftEmailSuppressionEntry {
  address: string;
  reason: EmailSuppressionReason;
  suppressedAt: string;
  detail: string;
}

export interface AstroliftEmailServiceDetail {
  managedServiceId: string;
  pluginSlug: string;
  region: string;
  identity: string;
  quota: AstroliftEmailSendQuota | null;
  accountStatus: AstroliftEmailAccountStatus | null;
  identityVerification: AstroliftEmailIdentityVerification | null;
  dnsAuthStatus: AstroliftEmailDnsAuthStatus | null;
  suppressionEntries: AstroliftEmailSuppressionEntry[];
  unsupportedNotes: string[];
}

// #488 Secret-change approval workflow --------------------------------

export type AstroliftSecretChangeApproval = Pick<
  GeneratedSecretChangeApproval,
  "id" | "approverUserId" | "approverDisplayName" | "decision" | "decidedAt" | "reason"
>;

export type AstroliftSecretChangeProposal = Pick<
  GeneratedSecretChangeProposal,
  | "id"
  | "registeredAppSlug"
  | "environmentName"
  | "op"
  | "status"
  | "proposerUserId"
  | "proposerDisplayName"
  | "requiredApproverCount"
  | "approvalsCount"
  | "expiresAt"
  | "decidedAt"
  | "appliedAt"
  | "applyError"
  | "createdAt"
> & {
  /** JSON scalar — varies by op (see SecretChangeOp). */
  payload: Record<string, unknown>;
  /** JSON scalar — pre-rendered before/after for the proposal page. */
  payloadDiff: Record<string, unknown>;
  approvals: AstroliftSecretChangeApproval[];
};
