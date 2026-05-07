// Types matching the AstroliftOrganization / AstroliftTeam /
// AstroliftProject Strawberry types in
// astrolift-api/astrolift_identity/schema/types.py.
//
// Hand-written until codegen lands; if the API schema drifts these
// types must drift with it. Run `make schema` in astrolift-api to
// regenerate the SDL when adding fields.

export type AstroliftGuid = string;

export interface AstroliftOrganization {
  id: AstroliftGuid;
  slug: string;
  name: string;
  website: string;
  scimEnabled: boolean;
  auditLogRetentionDays: number;
  previewMaxActiveDefault: number;
  logRetentionDaysDefault: number;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
}

export interface AstroliftTeam {
  id: AstroliftGuid;
  slug: string;
  name: string;
  organization: Pick<AstroliftOrganization, "id" | "slug" | "name">;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
}

export interface AstroliftProject {
  id: AstroliftGuid;
  slug: string;
  name: string;
  organization: Pick<AstroliftOrganization, "id" | "slug" | "name">;
  team: Pick<AstroliftTeam, "id" | "slug" | "name">;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
}

export interface AstroliftUser {
  id: string;
  username: string;
  email: string;
  isActive: boolean;
}

export type ScopeKind = "ORG" | "TEAM" | "PROJECT" | "APP";

export interface AstroliftRole {
  id: AstroliftGuid;
  slug: string;
  name: string;
  description: string;
  scopeLevel: ScopeKind;
  permissions: string[];
  isSystem: boolean;
}

export interface AstroliftMember {
  id: AstroliftGuid;
  user: AstroliftUser;
  scopeKind: ScopeKind;
  scopeId: string;
  isActive: boolean;
  lifecycle: string;
  joinedAt: string | null;
  lastSeenAt: string | null;
  createdAt: string;
  deletedAt: string | null;
}

export interface AstroliftRoleBinding {
  id: AstroliftGuid;
  user: AstroliftUser | null;
  groupExternalId: string;
  role: AstroliftRole;
  scopeKind: ScopeKind;
  scopeId: string;
  grantedAt: string;
  expiresAt: string | null;
  inherits: boolean;
}

export type PolicyEffect = "ALLOW" | "DENY";

export interface AstroliftPolicy {
  id: AstroliftGuid;
  slug: string;
  name: string;
  description: string;
  scopeLevel: ScopeKind;
  scopeId: string | null;
  effect: PolicyEffect;
  actionPattern: string;
  resourcePattern: Record<string, unknown>;
  conditions: unknown[];
  actorPattern: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
}

export interface AstroliftApiToken {
  id: AstroliftGuid;
  name: string;
  user: AstroliftUser;
  teamSlug: string | null;
  tokenLast4: string;
  scopes: string[];
  expiresAt: string | null;
  lastUsedAt: string | null;
  isRevoked: boolean;
  createdAt: string;
}

export interface AstroliftApiTokenPlaintext {
  apiToken: AstroliftApiToken;
  plaintext: string;
}

export interface MutationError {
  code: string;
  message: string;
  field: string | null;
}

export interface MutationResult<T> {
  ok: boolean;
  errors: MutationError[];
  data: T | null;
}
