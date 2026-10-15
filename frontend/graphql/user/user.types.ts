// Manual types for the user domain.
// Once GraphQL Codegen is configured, replace these with imports from __generated__/graphql.ts

// --- Schema shapes ---

export type UserProfile = {
  id: string;
  username: string | null;
};

// Server-authoritative module capability manifest (spec 34 §3.2.3,
// spec 36 §1.1). One entry per entitled module; the backend computes
// each flag from the viewer's effective permissions for the active
// tenant. Anonymous / no-tenant viewers get an empty list — never a
// partial or client-derived set.
export type ModuleEntitlement = {
  key: string;
  canView: boolean;
  canCreate: boolean;
  canManage: boolean;
  canRun: boolean;
  // Whether the module is switched on for the active organization (#1859).
  // Always true for apps/agents/workflows/admin; for the per-org modules
  // (chat_studio_integration, agent_live_attach) true only when an org
  // admin turned it on and the install has not forced it off.
  enabled: boolean;
  // False when the install's signed licence withholds this per-org module
  // (#1879): off for every org and an org admin cannot turn it on.
  licensed?: boolean;
  // Who last switched a per-org module: "org_admin" or "licence" (#1879).
  source?: string | null;
};

export type CurrentUser = {
  id: string;
  profile: UserProfile | null;
  modules: ModuleEntitlement[];
};

// --- Query types ---

export type MeQueryData = {
  me: CurrentUser | null;
};

export type MeQueryVariables = Record<string, never>;

// --- Mutation types (add as needed) ---
// export type UpdateProfileVariables = { input: { id: string; username: string } };
