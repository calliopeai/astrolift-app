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
