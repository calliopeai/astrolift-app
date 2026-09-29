/**
 * Effective access, read from role bindings (access UX design 3.2): the pure
 * half of a principal's Access tab and a team's. No React, no queries.
 *
 * What the backend gives and what it does not, which shapes this file:
 *
 * - A binding names its scope by kind and integer pk (`scopeId`), and the
 *   server resolves a label for it: `organization acme`, `team payments`,
 *   `project payments/checkout`, `app checkout`. The label is the only name
 *   the UI can read for a scope pk (teams and projects are exposed by GUID),
 *   so scopes are named and linked from it.
 * - A binding does not say which bindings cover it, so `coveredBy` works out
 *   the ones it can from the labels: an org grant of the same role covers
 *   every scope; a team grant covers that team's projects (the project label
 *   carries its team). An app's project is not in its label, so an app grant
 *   is only ever covered by the org.
 * - The list is the principal's bindings, all in hand (the bindings page
 *   holds them to the principal on the server), so the `can:` filter, sort
 *   and the numbered page run here, over at most the principal's own grants.
 */
import type { SortState } from "@/components/data-table";
import type { ListDefinition } from "@/components/list/list-state";
import {
  type GrantSourceInfo,
  type RoleRef,
  SCOPE_ORDER,
  type ScopeKind,
  type ScopeRef,
} from "@/components/access/access-model";
import type { AstroliftRole, AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { TEAMS_HREF } from "./access-nav";

// ---------------------------------------------------------------------------
// Scopes, from the server's labels
// ---------------------------------------------------------------------------

const LABEL_PREFIX: ReadonlyArray<[string, ScopeKind]> = [
  ["organization ", "ORG"],
  ["team ", "TEAM"],
  ["project ", "PROJECT"],
  ["app ", "APP"],
];

/** `team payments` → `{ TEAM, payments }`; null for a bare or unknown label. */
export function parseScopeLabel(label: string): { kind: ScopeKind; slug: string } | null {
  for (const [prefix, kind] of LABEL_PREFIX) {
    if (label.startsWith(prefix) && label.length > prefix.length) {
      return { kind, slug: label.slice(prefix.length) };
    }
  }
  return null;
}

/** Where a scope is looked at, so a grant's scope links to it. */
export function scopeHref(kind: ScopeKind, slug: string): string | undefined {
  switch (kind) {
    case "ORG":
      return "/administration/organization";
    case "TEAM":
      return `${TEAMS_HREF}/${encodeURIComponent(slug)}`;
    case "PROJECT":
      return `/administration/projects?q=${encodeURIComponent(slug.split("/").pop() ?? slug)}`;
    case "APP":
      return `/apps/${encodeURIComponent(slug)}`;
  }
}

/** The scope a binding is held at, named from its label where the server gave one. */
export function scopeOfBinding(b: AstroliftRoleBinding): ScopeRef & { slug: string | null } {
  const parsed = b.sourceScopeLabel ? parseScopeLabel(b.sourceScopeLabel) : null;
  if (parsed && parsed.kind === b.scopeKind) {
    return {
      kind: b.scopeKind,
      id: b.scopeId,
      name: parsed.slug,
      slug: parsed.slug,
      href: scopeHref(parsed.kind, parsed.slug),
    };
  }
  return { kind: b.scopeKind, id: b.scopeId, name: `#${b.scopeId}`, slug: null };
}

/**
 * Team pk → team slug, from the TEAM-scope bindings' labels. Member rows at
 * TEAM scope carry only the pk, so this is the only way to name the team a
 * membership is on; a team where nobody holds a binding stays unnamed.
 */
export function teamSlugIndex(bindings: readonly AstroliftRoleBinding[]): Map<string, string> {
  const index = new Map<string, string>();
  for (const b of bindings) {
    if (b.scopeKind !== "TEAM" || !b.sourceScopeLabel) continue;
    const parsed = parseScopeLabel(b.sourceScopeLabel);
    if (parsed?.kind === "TEAM") index.set(b.scopeId, parsed.slug);
  }
  return index;
}

// ---------------------------------------------------------------------------
// Access rows
// ---------------------------------------------------------------------------

export interface AccessRow {
  id: string;
  binding: AstroliftRoleBinding;
  scope: ScopeRef & { slug: string | null };
  /** The role with its permissions, where the role catalog has it. */
  role: RoleRef | null;
  source: GrantSourceInfo;
  /** A wider grant of the same role that already gives this: remove both to remove it. */
  coveredBy: ScopeRef | null;
}

export function roleRefOf(role: AstroliftRole): RoleRef {
  return { ...role, scopeLevel: role.scopeLevel as ScopeKind };
}

function covering(row: AccessRow, all: readonly AccessRow[]): ScopeRef | null {
  if (row.scope.kind === "ORG") return null;
  const teamOfProject = row.scope.kind === "PROJECT" ? row.scope.slug?.split("/")[0] : undefined;
  const hit = all.find(
    (other) =>
      other.id !== row.id &&
      other.binding.role.id === row.binding.role.id &&
      (other.scope.kind === "ORG" ||
        (teamOfProject !== undefined &&
          other.scope.kind === "TEAM" &&
          other.scope.slug === teamOfProject))
  );
  return hit ? hit.scope : null;
}

/**
 * One row per binding. The bindings are the principal's own, so every row's
 * source is direct; what reaches a person through their IdP groups shows on
 * each group's page and on an object's Access tab.
 */
export function buildAccessRows(
  bindings: readonly AstroliftRoleBinding[],
  roles: readonly AstroliftRole[] = []
): AccessRow[] {
  const byId = new Map(roles.map((r) => [r.id, r]));
  const rows: AccessRow[] = bindings.map((binding) => {
    const role = byId.get(binding.role.id);
    return {
      id: binding.id,
      binding,
      scope: scopeOfBinding(binding),
      role: role ? roleRefOf(role) : null,
      source: {},
      coveredBy: null,
    };
  });
  return rows.map((row) => ({ ...row, coveredBy: covering(row, rows) }));
}

// ---------------------------------------------------------------------------
// The list: declaration, filter, sort, page
// ---------------------------------------------------------------------------

export const SCOPE_GROUP_LABEL: Record<ScopeKind, string> = {
  ORG: "Organization",
  TEAM: "Teams",
  PROJECT: "Projects",
  APP: "Apps",
};

export const ACCESS_LIST: ListDefinition = {
  id: "admin.access.principal",
  fields: [
    {
      key: "scope",
      label: "Scope",
      options: SCOPE_ORDER.map((k) => ({ value: k, label: SCOPE_GROUP_LABEL[k] })),
    },
    { key: "role", label: "Role" },
    { key: "can", label: "Can" },
  ],
  searchPlaceholder: "Search roles and scopes…  can:app.deploy",
  // Resolver order, widest first: the order in which "why can they?" is answered.
  defaultSort: [{ key: "scope", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** `can:app.deploy`, or `can:app.*` for any of a resource's verbs. */
export function grantsPermission(row: AccessRow, permission: string): boolean {
  const held = row.role?.permissions ?? [];
  if (permission.endsWith("*")) {
    const prefix = permission.slice(0, -1);
    return held.some((p) => p.startsWith(prefix));
  }
  return held.includes(permission);
}

function matches(row: AccessRow, filters: Record<string, string>, q: string): boolean {
  if (filters.scope && row.scope.kind !== filters.scope) return false;
  if (filters.role) {
    const want = filters.role.toLowerCase();
    if (
      row.binding.role.slug.toLowerCase() !== want &&
      row.binding.role.name.toLowerCase() !== want
    )
      return false;
  }
  if (filters.can && !grantsPermission(row, filters.can)) return false;
  if (q) {
    const hay = [
      row.binding.role.name,
      row.binding.role.slug,
      row.scope.name,
      row.binding.scopeKind,
      row.binding.user?.username ?? row.binding.groupExternalId,
    ]
      .join(" ")
      .toLowerCase();
    if (!hay.includes(q.toLowerCase())) return false;
  }
  return true;
}

const SORT_VALUE: Record<string, (r: AccessRow) => string | number> = {
  scope: (r) => `${SCOPE_ORDER.indexOf(r.scope.kind)}:${r.scope.name.toLowerCase()}`,
  role: (r) => r.binding.role.name.toLowerCase(),
  principal: (r) => (r.binding.user?.username ?? r.binding.groupExternalId).toLowerCase(),
  granted: (r) => Date.parse(r.binding.grantedAt) || 0,
  // Never expiring sorts last.
  expires: (r) => (r.binding.expiresAt ? Date.parse(r.binding.expiresAt) : Number.MAX_SAFE_INTEGER),
};

function compare(a: AccessRow, b: AccessRow, sort: SortState[]): number {
  for (const s of sort) {
    const value = SORT_VALUE[s.key];
    if (!value) continue;
    const x = value(a);
    const y = value(b);
    if (x < y) return s.dir === "asc" ? -1 : 1;
    if (x > y) return s.dir === "asc" ? 1 : -1;
  }
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

export function selectAccess(
  rows: readonly AccessRow[],
  {
    filters,
    q,
    sort,
    page,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): { rows: AccessRow[]; totalCount: number; filtered: AccessRow[] } {
  const filtered = rows
    .filter((r) => matches(r, filters, q.trim()))
    .sort((a, b) => compare(a, b, sort));
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: filtered.slice(start, start + pageSize), totalCount: filtered.length, filtered };
}

/**
 * What a principal holds, counted over every binding (not the filtered page):
 * a summary that changes as you type is not a summary of what they hold.
 */
export function summarizeAccess(rows: readonly AccessRow[]): string | null {
  if (rows.length === 0) return null;
  const roles = new Set(rows.map((r) => r.binding.role.id)).size;
  const byScope = SCOPE_ORDER.map((k) => ({
    k,
    n: rows.filter((r) => r.scope.kind === k).length,
  })).filter((x) => x.n > 0);
  const covered = rows.filter((r) => r.coveredBy).length;
  const expiring = rows.filter((r) => r.binding.expiresAt).length;
  const parts = [
    `${roles} ${roles === 1 ? "role" : "roles"} across ${rows.length} ${rows.length === 1 ? "grant" : "grants"}`,
    byScope.map((x) => `${x.n} at ${SCOPE_GROUP_LABEL[x.k].toLowerCase()}`).join(", "),
  ];
  if (covered > 0) parts.push(`${covered} also granted wider`);
  if (expiring > 0) parts.push(`${expiring} expiring`);
  return parts.join("; ");
}

/** Who loses access when a binding goes, for the remove confirm. */
export function holderLabel(b: AstroliftRoleBinding): string {
  return b.user?.username ?? b.groupExternalId;
}
