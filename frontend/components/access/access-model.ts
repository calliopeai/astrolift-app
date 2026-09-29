/**
 * The access model the shared access pieces speak (docs: the access UX
 * design, section 1). Pure: types and the helpers that turn the backend's
 * shapes (role bindings, role permission lists, the nav tree) into what the
 * pieces show. No React, no queries.
 *
 *   Principal   who holds access: a user, an IdP group, a team or a token
 *   Scope       where: ORG › TEAM › PROJECT › APP, granting down the chain
 *   Role        what: a set of `<resource>.<verb>` catalog slugs
 *   Source      why: direct, via a group or team, or inherited from above
 */

import type { AstroliftRoleBinding, ScopeKind } from "@/graphql/identity/identity.types";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";

export type { ScopeKind };

// ---------------------------------------------------------------------------
// Principals
// ---------------------------------------------------------------------------

export type PrincipalKind = "user" | "group" | "team" | "token";

export interface Principal {
  kind: PrincipalKind;
  /** The stable id shown in mono: user pk, IdP group external id, team slug, token prefix. */
  id: string;
  /** What people call it: display name, group name, team name, token name. */
  name: string;
  /** A second line where it helps: an email, the IdP, a member count. */
  detail?: string;
  href?: string;
}

export const PRINCIPAL_NOUN: Record<PrincipalKind, string> = {
  user: "user",
  group: "group",
  team: "team",
  token: "token",
};

/** The principal a binding is held by: its user, or its IdP group. */
export function principalOfBinding(binding: AstroliftRoleBinding): Principal {
  if (binding.user) {
    return {
      kind: "user",
      id: binding.user.id,
      name: binding.user.username,
      detail: binding.user.email || undefined,
    };
  }
  return { kind: "group", id: binding.groupExternalId, name: binding.groupExternalId };
}

// ---------------------------------------------------------------------------
// Scopes
// ---------------------------------------------------------------------------

/** The resolver's order, widest first (permission_resolver._INHERITANCE_ORDER). */
export const SCOPE_ORDER: readonly ScopeKind[] = ["ORG", "TEAM", "PROJECT", "APP"];

export const SCOPE_NOUN: Record<ScopeKind, string> = {
  ORG: "org",
  TEAM: "team",
  PROJECT: "project",
  APP: "app",
};

export interface ScopeRef {
  kind: ScopeKind;
  id: string;
  name: string;
  href?: string;
}

/** A node of the scope tree. `children` undefined with `hasChildren` means not loaded yet. */
export interface ScopeNode extends ScopeRef {
  slug?: string;
  hasChildren?: boolean;
  children?: ScopeNode[];
}

/** The path from the root to the node with `id`, or null when it is not in the loaded tree. */
export function scopePath(roots: ScopeNode[], kind: ScopeKind, id: string): ScopeNode[] | null {
  for (const node of roots) {
    if (node.kind === kind && node.id === id) return [node];
    const below = node.children ? scopePath(node.children, kind, id) : null;
    if (below) return [node, ...below];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Sources: why a principal has a grant where it is being looked at
// ---------------------------------------------------------------------------

export type GrantVia =
  | { kind: "group"; group: string; href?: string }
  | { kind: "team"; team: string; href?: string };

export interface GrantSourceInfo {
  /** Held through membership rather than by the principal itself. */
  via?: GrantVia;
  /** Bound on an ancestor of the scope being looked at, not on it. */
  inheritedFrom?: ScopeRef;
}

/** "direct", "via group okta:eng", "inherited from org acme", or both parts. */
export function describeSource(source: GrantSourceInfo): string {
  const parts: string[] = [];
  if (source.via) parts.push(`via ${source.via.kind} ${viaName(source.via)}`);
  if (source.inheritedFrom) {
    parts.push(
      `inherited from ${SCOPE_NOUN[source.inheritedFrom.kind]} ${source.inheritedFrom.name}`
    );
  }
  return parts.length ? parts.join(", ") : "direct";
}

export function viaName(via: GrantVia): string {
  return via.kind === "group" ? via.group : via.team;
}

/**
 * The source of `binding` as seen from `viewed`. A binding on another scope
 * than the one being looked at is inherited from it (the resolver grants
 * down); a group binding is held via that group. The binding's own scope is
 * named by `sourceScopeLabel` where the backend supplies it.
 */
export function sourceOfBinding(
  binding: AstroliftRoleBinding,
  viewed?: { kind: ScopeKind; id: string }
): GrantSourceInfo {
  const source: GrantSourceInfo = {};
  if (!binding.user && binding.groupExternalId) {
    source.via = { kind: "group", group: binding.groupExternalId };
  }
  if (viewed && (binding.scopeKind !== viewed.kind || binding.scopeId !== viewed.id)) {
    source.inheritedFrom = {
      kind: binding.scopeKind,
      id: binding.scopeId,
      name: binding.sourceScopeLabel || `${SCOPE_NOUN[binding.scopeKind]} ${binding.scopeId}`,
    };
  }
  return source;
}

// ---------------------------------------------------------------------------
// Roles and the permission catalog
// ---------------------------------------------------------------------------

export interface RoleRef {
  id: string;
  slug: string;
  name: string;
  description?: string;
  scopeLevel: ScopeKind;
  permissions: string[];
  isSystem: boolean;
}

/**
 * A role binds at its own level or narrower: an org role on a team, a
 * project or an app; an app role on an app only. The reason when it cannot.
 */
export function canBindAt(
  role: Pick<RoleRef, "name" | "scopeLevel">,
  kind: ScopeKind
): true | string {
  return SCOPE_ORDER.indexOf(kind) >= SCOPE_ORDER.indexOf(role.scopeLevel)
    ? true
    : `${role.name} is a ${SCOPE_NOUN[role.scopeLevel]} role: pick a ${SCOPE_NOUN[role.scopeLevel]} or something inside one.`;
}

export interface PermissionArea {
  key: string;
  label: string;
  resources: string[];
}

/** Areas of the matrix (design 3.5), in the nav's order. Unlisted resources fall into Other. */
export const PERMISSION_AREAS: readonly PermissionArea[] = [
  {
    key: "agents",
    label: "Agents",
    resources: ["agent", "agent_task", "agent_box", "agent_env_spec", "skill"],
  },
  {
    key: "apps",
    label: "Apps",
    resources: ["app", "secret", "managed_service", "deploy_token", "form"],
  },
  { key: "workflows", label: "Workflows", resources: ["workflow", "pipeline"] },
  {
    key: "admin",
    label: "Admin",
    resources: [
      "org",
      "team",
      "project",
      "api_token",
      "webhook",
      "cluster",
      "provider_plugin",
      "scm",
      "audit_log",
      "billing",
      "zentinelle",
      "admin",
    ],
  },
];

/** The verb columns every resource shares; each column takes the first slug whose verb matches. */
export const VERB_COLUMNS: ReadonlyArray<{ key: string; label: string; verbs: string[] }> = [
  { key: "read", label: "Read", verbs: ["read"] },
  { key: "create", label: "Create", verbs: ["create", "register"] },
  { key: "update", label: "Update", verbs: ["update", "write"] },
  { key: "delete", label: "Delete", verbs: ["delete", "destroy", "unregister"] },
];

export function splitSlug(slug: string): { resource: string; verb: string } {
  const dot = slug.indexOf(".");
  return dot < 0
    ? { resource: "other", verb: slug }
    : { resource: slug.slice(0, dot), verb: slug.slice(dot + 1) };
}

export interface MatrixRow {
  resource: string;
  /** Column key to the slug in it; absent where the resource has no such verb. */
  cells: Record<string, string>;
  /** The resource's own verbs (deploy, rollback, exec_pod…), in catalog order. */
  more: string[];
  all: string[];
}

export interface MatrixArea {
  key: string;
  label: string;
  rows: MatrixRow[];
}

/** The catalog laid out as areas × verbs. Pure; the catalog order is kept within a row. */
export function buildMatrix(catalog: readonly string[] = ASTROLIFT_PERMISSIONS): MatrixArea[] {
  const byResource = new Map<string, string[]>();
  for (const slug of catalog) {
    const { resource } = splitSlug(slug);
    byResource.set(resource, [...(byResource.get(resource) ?? []), slug]);
  }
  const known = new Set(PERMISSION_AREAS.flatMap((a) => a.resources));
  const other = [...byResource.keys()].filter((r) => !known.has(r)).sort();
  const areas: PermissionArea[] = [
    ...PERMISSION_AREAS,
    ...(other.length ? [{ key: "other", label: "Other", resources: other }] : []),
  ];

  return areas
    .map((area) => ({
      key: area.key,
      label: area.label,
      rows: area.resources
        .filter((r) => byResource.has(r))
        .map((resource) => {
          const all = byResource.get(resource) ?? [];
          const cells: Record<string, string> = {};
          const used = new Set<string>();
          for (const col of VERB_COLUMNS) {
            const hit = all.find((s) => !used.has(s) && col.verbs.includes(splitSlug(s).verb));
            if (hit) {
              cells[col.key] = hit;
              used.add(hit);
            }
          }
          return { resource, cells, more: all.filter((s) => !used.has(s)), all };
        }),
    }))
    .filter((area) => area.rows.length > 0);
}

export interface PermissionDiff {
  added: string[];
  removed: string[];
}

export function diffPermissions(
  current: readonly string[],
  base: readonly string[]
): PermissionDiff {
  const now = new Set(current);
  const was = new Set(base);
  return {
    added: [...now].filter((p) => !was.has(p)).sort(),
    removed: [...was].filter((p) => !now.has(p)).sort(),
  };
}

const RESOURCE_LABEL: Record<string, string> = {
  app: "apps",
  agent: "agents",
  agent_task: "agent runs",
  agent_box: "agent boxes",
  agent_env_spec: "agent environments",
  secret: "secrets",
  managed_service: "managed services",
  deploy_token: "deploy tokens",
  api_token: "API tokens",
  audit_log: "the audit log",
  provider_plugin: "providers",
  scm: "source control",
  org: "the organization",
};

export function resourceLabel(resource: string): string {
  return RESOURCE_LABEL[resource] ?? `${resource.replace(/_/g, " ")}s`;
}

function verbLabel(verb: string): string {
  return verb.replace(/_/g, " ");
}

/**
 * A role in plain words (design 3.4, step 2): "Read only: apps, agents" or
 * "apps: read, deploy, rollback; secrets: read". The role's own description
 * wins when it has one; this is the fallback and the matrix's caption.
 */
export function summarizePermissions(
  permissions: readonly string[],
  catalog: readonly string[] = ASTROLIFT_PERMISSIONS
): string {
  if (permissions.length === 0) return "No permissions";
  const held = new Set(permissions);
  if (catalog.length > 0 && catalog.every((p) => held.has(p))) return "Everything in the catalog";

  const byResource = new Map<string, string[]>();
  for (const slug of permissions) {
    const { resource, verb } = splitSlug(slug);
    byResource.set(resource, [...(byResource.get(resource) ?? []), verb]);
  }
  const resources = [...byResource.keys()];
  if ([...byResource.values()].every((verbs) => verbs.every((v) => v === "read"))) {
    return `Read only: ${resources.map(resourceLabel).join(", ")}`;
  }
  return resources
    .map((r) => `${resourceLabel(r)}: ${(byResource.get(r) ?? []).map(verbLabel).join(", ")}`)
    .join("; ");
}
