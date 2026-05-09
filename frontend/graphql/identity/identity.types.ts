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
  AstroliftApiToken as GeneratedApiToken,
  AstroliftApiTokenPlaintext as GeneratedApiTokenPlaintext,
  AstroliftIdentityProvider as GeneratedIdentityProvider,
  AstroliftInvitation as GeneratedInvitation,
  AstroliftMember as GeneratedMember,
  AstroliftMyProfile as GeneratedMyProfile,
  AstroliftOrganization as GeneratedOrganization,
  AstroliftPolicy as GeneratedPolicy,
  AstroliftProject as GeneratedProject,
  AstroliftRole as GeneratedRole,
  AstroliftRoleBinding as GeneratedRoleBinding,
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

export type AstroliftProject = Omit<
  GeneratedProject,
  "organization" | "team"
> & {
  organization: Pick<AstroliftOrganization, "id" | "slug" | "name">;
  team: Pick<AstroliftTeam, "id" | "slug" | "name">;
};

export type AstroliftUser = GeneratedUser;

export type AstroliftRole = Omit<GeneratedRole, "scopeLevel"> & {
  scopeLevel: ScopeKind;
};

export type AstroliftMember = Omit<GeneratedMember, "scopeKind"> & {
  scopeKind: ScopeKind;
};

export type AstroliftRoleBinding = Omit<GeneratedRoleBinding, "scopeKind"> & {
  scopeKind: ScopeKind;
};

export type AstroliftPolicy = Omit<
  GeneratedPolicy,
  "scopeLevel" | "effect"
> & {
  scopeLevel: ScopeKind;
  effect: PolicyEffect;
};

export type AstroliftIdentityProvider = Omit<
  GeneratedIdentityProvider,
  "kind"
> & {
  kind: IdpKind;
};

export type AstroliftApiToken = GeneratedApiToken;

export type InvitationStatus = "pending" | "accepted" | "expired" | "revoked";

export type AstroliftInvitation = Omit<
  GeneratedInvitation,
  "scopeKind" | "status"
> & {
  scopeKind: ScopeKind;
  status: InvitationStatus;
};

export type AstroliftApiTokenPlaintext = GeneratedApiTokenPlaintext;

export type MutationError = GeneratedMutationError;

export interface MutationResult<T> {
  ok: boolean;
  errors: MutationError[];
  data: T | null;
}
