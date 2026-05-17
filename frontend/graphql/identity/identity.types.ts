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
  AstroliftIdentityProvider as GeneratedIdentityProvider,
  AstroliftInvitation as GeneratedInvitation,
  AstroliftLogoutAllSessionsPayload as GeneratedLogoutAllSessionsPayload,
  AstroliftMember as GeneratedMember,
  AstroliftMyProfile as GeneratedMyProfile,
  AstroliftOrganization as GeneratedOrganization,
  AstroliftOrganizationAllowlistedDomain as GeneratedOrganizationAllowlistedDomain,
  AstroliftPolicy as GeneratedPolicy,
  AstroliftProject as GeneratedProject,
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

export type AstroliftMyProfile = GeneratedMyProfile;

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
  /**
   * Optional username of the operator who created this policy. Not
   * yet on the GraphQL `AstroliftPolicy` type — the policies table
   * surfaces it as "—" until the backend exposes
   * `created_by_username` (tracked in #415 backend follow-on).
   */
  createdByUsername?: string | null;
};

export type AstroliftIdentityProvider = Omit<GeneratedIdentityProvider, "kind"> & {
  kind: IdpKind;
  /**
   * Username of the operator who last flipped this provider to
   * active. Not yet on the GraphQL `AstroliftIdentityProvider`
   * type — surfaced only when the backend wires it in via the
   * #415 follow-on; until then `updatedAt` is the closest proxy
   * for "active since".
   */
  lastSwitchedByUsername?: string | null;
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

export type AstroliftActiveSession = GeneratedActiveSession;

export type AstroliftLogoutAllSessionsPayload = GeneratedLogoutAllSessionsPayload;

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
