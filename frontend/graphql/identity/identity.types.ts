/**
 * Identity types — facade over the codegen output.
 *
 * Entity shapes flow from `__generated__/schema.ts`. Narrow string
 * unions (ScopeKind, IdpKind, PolicyEffect) live here as frontend
 * switch-exhaustiveness aids; the schema carries them as plain
 * `String!`. Nested-object fields (`team.organization`, etc.) are
 * narrowed via `Pick<…, "id" | "slug" | "name">` because
 * operation-typing isn't wired in this codegen run — at the schema
 * level every type has all fields, but our queries only fetch a
 * subset. Phase 2 will move these narrows to per-operation types.
 */

import type {
  AstroliftActiveSession as GeneratedActiveSession,
  AstroliftApiToken as GeneratedApiToken,
  AstroliftApiTokenPlaintext as GeneratedApiTokenPlaintext,
  AstroliftApproverUser as GeneratedApproverUser,
  AstroliftAppSummary as GeneratedAppSummary,
  AstroliftEnrollmentQrPayload as GeneratedEnrollmentQrPayload,
  AstroliftHeartbeatSessionPayload as GeneratedHeartbeatSessionPayload,
  AstroliftIdentityProvider as GeneratedIdentityProvider,
  AstroliftInvitation as GeneratedInvitation,
  AstroliftLogoutAllSessionsPayload as GeneratedLogoutAllSessionsPayload,
  AstroliftMember as GeneratedMember,
  AstroliftMyProfile as GeneratedMyProfile,
  AstroliftOrganization as GeneratedOrganization,
  AstroliftOrganizationAllowlistedDomain as GeneratedOrganizationAllowlistedDomain,
  AstroliftPolicy as GeneratedPolicy,
  AstroliftProject as GeneratedProject,
  AstroliftRevokeAstroliftSessionPayload as GeneratedRevokeAstroliftSessionPayload,
  AstroliftRole as GeneratedRole,
  AstroliftRoleBinding as GeneratedRoleBinding,
  AstroliftSearchableUser as GeneratedSearchableUser,
  AstroliftTeam as GeneratedTeam,
  AstroliftUser as GeneratedUser,
  MutationError as GeneratedMutationError,
} from "@/graphql/__generated__/schema";

export type AstroliftGuid = string;

export type ScopeKind = "ORG" | "TEAM" | "PROJECT" | "APP";

export type PolicyEffect = "ALLOW" | "DENY";

export type IdpKind =
  | "oidc"
  | "saml"
  | "cognito"
  | "auth0"
  | "okta"
  | "azure_ad"
  | "google"
  | "github"
  | "local";

export type AstroliftOrganization = GeneratedOrganization;

// Extend the generated profile type with the timezone field that
// the codegen doesn't know about yet (pending `make schema + codegen`).
export type AstroliftMyProfile = GeneratedMyProfile & {
  timezone?: string | null;
};

export type AstroliftTeam = Omit<GeneratedTeam, "organization"> & {
  organization: Pick<AstroliftOrganization, "id" | "slug" | "name">;
};

export type AstroliftProject = Omit<GeneratedProject, "organization" | "team"> & {
  organization: Pick<AstroliftOrganization, "id" | "slug" | "name">;
  team: Pick<AstroliftTeam, "id" | "slug" | "name">;
};

/**
 * App status enum mirrors RegisteredApp.ProvisioningStatus on the
 * backend (pending/provisioning/ready/failed). The schema exposes it
 * as a free `String!` so narrow it here for switch exhaustiveness.
 */
export type AstroliftAppStatus = "pending" | "provisioning" | "ready" | "failed";

export type AstroliftAppSummary = Omit<GeneratedAppSummary, "status"> & {
  status: AstroliftAppStatus;
  /** True when the app is agent-backed (has a kind=agent workload) — the
   * sidebar renders it with a bot icon instead of the app rocket. */
  isAgent: boolean;
};

export interface AstroliftNavTreeProjectNode {
  project: Pick<AstroliftProject, "id" | "slug" | "name">;
  apps: AstroliftAppSummary[];
}

export interface AstroliftNavTreeTeamNode {
  team: Pick<AstroliftTeam, "id" | "slug" | "name">;
  projects: AstroliftNavTreeProjectNode[];
  unassignedApps: AstroliftAppSummary[];
}

export interface AstroliftNavTree {
  organization: Pick<AstroliftOrganization, "id" | "slug" | "name">;
  teams: AstroliftNavTreeTeamNode[];
  unassignedApps: AstroliftAppSummary[];
}

export type AstroliftUser = GeneratedUser;

/**
 * Row shape used by the approval-policy picker (#410) — surfaces
 * display_name + avatar_url so the picker can render an identifiable
 * chip without a follow-up lookup. Backed by
 * `astroliftOrgMembersForApprovalPicker(orgSlug)`.
 */
export type AstroliftApproverUser = GeneratedApproverUser;

export type AstroliftRole = Omit<GeneratedRole, "scopeLevel"> & {
  scopeLevel: ScopeKind;
};

export type AstroliftMember = Omit<GeneratedMember, "scopeKind"> & {
  scopeKind: ScopeKind;
};

export type AstroliftRoleBinding = Omit<GeneratedRoleBinding, "scopeKind"> & {
  scopeKind: ScopeKind;
};

export type AstroliftPolicy = Omit<GeneratedPolicy, "scopeLevel" | "effect"> & {
  scopeLevel: ScopeKind;
  effect: PolicyEffect;
};

export type AstroliftIdentityProvider = Omit<GeneratedIdentityProvider, "kind"> & {
  kind: IdpKind;
};

export type AstroliftApiToken = GeneratedApiToken;

export type InvitationStatus = "pending" | "accepted" | "expired" | "revoked";

export type AstroliftInvitation = Omit<GeneratedInvitation, "scopeKind" | "status"> & {
  scopeKind: ScopeKind;
  status: InvitationStatus;
};

/**
 * Row in the invite-flow de-dupe search (#418). ``matchKind`` is a
 * narrow string union the FE uses to switch which CTA renders (grant
 * role to this user vs. resend / cancel the pending invitation).
 */
export type InvitationSearchMatchKind = "MEMBER" | "INVITATION";

export type AstroliftSearchableUser = Omit<GeneratedSearchableUser, "matchKind"> & {
  matchKind: InvitationSearchMatchKind;
};

export type AstroliftApiTokenPlaintext = GeneratedApiTokenPlaintext;

export type AstroliftOrganizationAllowlistedDomain = GeneratedOrganizationAllowlistedDomain;

/**
 * Issuance kind of a session row. Mirrors
 * ``astrolift_identity.models.ClientKind`` on the backend. Stored
 * as the lowercase string on the wire; the FE narrows it here so
 * switch / badge rendering stays exhaustive when a new kind lands.
 */
export type AstroliftClientKind = "web" | "cli" | "mobile" | "browser_extension" | "api_token";

export type AstroliftActiveSession = Omit<GeneratedActiveSession, "clientKind"> & {
  clientKind: AstroliftClientKind;
};

export type AstroliftLogoutAllSessionsPayload = GeneratedLogoutAllSessionsPayload;

// #494 — mobile install enrollment QR payload (returned by
// ``generateInstallEnrollmentQr``). All scalars are non-null per
// the backend ``EnrollmentQrPayloadType`` — including ``qrSvg``,
// which is the server-rendered SVG markup the frontend embeds
// directly.
export type AstroliftEnrollmentQrPayload = GeneratedEnrollmentQrPayload;
export type AstroliftRevokeAstroliftSessionPayload = GeneratedRevokeAstroliftSessionPayload;

export type AstroliftHeartbeatSessionPayload = GeneratedHeartbeatSessionPayload;

export type MutationError = GeneratedMutationError;

export interface MutationResult<T> {
  ok: boolean;
  errors: MutationError[];
  data: T | null;
}

/** One row in the per-user "connected accounts" surface of the account drawer. */
export interface AstroliftMyConnectedAccount {
  providerConfigId: string;
  providerKind: string;
  providerLabel: string;
  isConnected: boolean;
  linkedAccountLogin: string | null;
  reauthRequired: boolean;
  expiresAt: string | null;
  lastUsedAt: string | null;
}

/** Payload returned by `astroliftConnectUserSourceProvider`. */
export interface AstroliftConnectUserSourceProviderPayload {
  providerConfigId: string;
  authorizationUrl: string;
}

/** Payload returned by `astroliftDisconnectUserSourceProvider`. */
export interface AstroliftDisconnectUserSourceProviderPayload {
  providerConfigId: string;
  disconnectedId: string | null;
}

/** Per-id outcome row for a bulk identity mutation. */
export interface AstroliftBulkOpItemResult {
  id: AstroliftGuid;
  ok: boolean;
  alreadyExisted: boolean;
  errors: MutationError[];
}

/** Payload of `bulkRevokeAstroliftRoleBindings`. */
export interface AstroliftBulkRevokeRoleBindingsPayload {
  results: AstroliftBulkOpItemResult[];
  revokedCount: number;
  failedCount: number;
}

/** Payload of `bulkAssignAstroliftTeamMemberRoles`. */
export interface AstroliftBulkAssignTeamMemberRolesPayload {
  results: AstroliftBulkOpItemResult[];
  assignedCount: number;
  alreadyAssignedCount: number;
  failedCount: number;
}

// ---- #487 step-up auth ------------------------------------------------

export type AstroliftElevationMethod =
  | "password"
  | "otp"
  | "webauthn"
  | "magic_link";

export interface ElevateAdminSessionInput {
  method: AstroliftElevationMethod;
  credential: string;
  ttlSeconds?: number;
}

export interface AstroliftElevatePayload {
  elevatedUntil: string;
  secondsRemaining: number;
  method: AstroliftElevationMethod;
}

export interface AstroliftDeelevatePayload {
  previouslyElevated: boolean;
}

export interface AstroliftElevationStatus {
  elevated: boolean;
  elevatedUntil: string | null;
  secondsRemaining: number;
  method: AstroliftElevationMethod | null;
  requiredFor: string[];
}
