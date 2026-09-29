/**
 * Admin › Access › People (access UX design 3.1): one list of principals.
 * Users and IdP groups are rows of the same list; a pending invitation is a
 * user row in the Invited view, so invitation history is a view, not a
 * second table. Pure: the declaration, the row model, filter, sort, page.
 *
 * The backend's member, binding and invitation pages take `search`, `limit`
 * and `after` only, so the hook walks them (`useWalk`) and `selectPeople`
 * answers the views, the filter chips, the sort and the numbered page, as the
 * Clusters list does. What each part can know from today's API:
 *
 * - Users: one row per user (a user holds several Member rows: ORG, and the
 *   TEAM / APP rows grants create). Their roles are the role bindings held
 *   by their user.
 * - Groups: only IdP groups that hold a role binding are known; the org's
 *   full group list and its members need the IdP sync (design 6.4).
 * - Teams: a TEAM-scope Member row carries the team's integer pk, which is
 *   named from the TEAM bindings' labels (`teamSlugIndex`); a team where
 *   nobody holds a binding stays unnamed, but Mine still matches on the pk.
 * - Admins: users and groups holding, at org scope, a role that can manage
 *   members (`org.manage_members`).
 */
import type { SortState } from "@/components/data-table";
import type { Principal } from "@/components/access/access-model";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type {
  AstroliftInvitation,
  AstroliftMember,
  AstroliftRole,
  AstroliftRoleBinding,
  AstroliftUser,
} from "@/graphql/identity/identity.types";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";
import { teamSlugIndex } from "@/components/screens/administration/access/principal-access";

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

export interface UserRow {
  kind: "user";
  key: string;
  /** The member row the detail page opens on: the ORG row where there is one. */
  memberId: string;
  user: AstroliftUser;
  memberships: AstroliftMember[];
  bindings: AstroliftRoleBinding[];
  /** TEAM-scope memberships: pk, and slug where a binding label names it. */
  teams: { pk: string; slug: string | null }[];
  lifecycle: string;
  lastActiveAt: string | null;
  joinedAt: string;
  admin: boolean;
}

export interface GroupRow {
  kind: "group";
  key: string;
  externalId: string;
  bindings: AstroliftRoleBinding[];
  admin: boolean;
}

export interface InvitationRow {
  kind: "invitation";
  key: string;
  invitation: AstroliftInvitation;
}

export type PeopleRow = UserRow | GroupRow | InvitationRow;

/** The permission that makes a holder an org admin, for the Admins view. */
export const ADMIN_PERMISSION = "org.manage_members";

function isAdminBinding(b: AstroliftRoleBinding, adminRoles: Set<string>): boolean {
  return b.scopeKind === "ORG" && adminRoles.has(b.role.id);
}

/** The route param for a group's page: `group:<external id>`. */
export function groupParam(externalId: string): string {
  return `group:${externalId}`;
}

export function principalHref(row: UserRow | GroupRow): string {
  return row.kind === "user"
    ? `${PEOPLE_HREF}/${row.memberId}`
    : `${PEOPLE_HREF}/${encodeURIComponent(groupParam(row.externalId))}`;
}

export function principalOf(row: PeopleRow): Principal {
  switch (row.kind) {
    case "user":
      return {
        kind: "user",
        id: row.user.id,
        name: row.user.username,
        detail: row.user.email || undefined,
      };
    case "group":
      return {
        kind: "group",
        id: row.externalId,
        name: row.externalId,
        detail: `IdP group · ${row.bindings.length} ${row.bindings.length === 1 ? "grant" : "grants"}`,
      };
    case "invitation":
      return {
        kind: "user",
        id: row.invitation.email,
        name: row.invitation.email,
        detail:
          row.invitation.invitedByDisplayName ?? row.invitation.invitedByUsername ?? undefined,
      };
  }
}

/**
 * Users (one row each) and the IdP groups that hold a binding, from the
 * walked member and binding rows.
 */
export function buildPeopleRows({
  members,
  bindings,
  roles,
}: {
  members: readonly AstroliftMember[];
  bindings: readonly AstroliftRoleBinding[];
  roles: readonly AstroliftRole[];
}): { users: UserRow[]; groups: GroupRow[] } {
  const adminRoles = new Set(
    roles.filter((r) => r.permissions.includes(ADMIN_PERMISSION)).map((r) => r.id)
  );
  const teamNames = teamSlugIndex(bindings);

  const byUser = new Map<string, AstroliftRoleBinding[]>();
  const byGroup = new Map<string, AstroliftRoleBinding[]>();
  for (const b of bindings) {
    if (b.user) byUser.set(b.user.id, [...(byUser.get(b.user.id) ?? []), b]);
    else if (b.groupExternalId)
      byGroup.set(b.groupExternalId, [...(byGroup.get(b.groupExternalId) ?? []), b]);
  }

  const memberships = new Map<string, AstroliftMember[]>();
  for (const m of members) memberships.set(m.user.id, [...(memberships.get(m.user.id) ?? []), m]);

  const users: UserRow[] = [...memberships.entries()].map(([userId, rows]) => {
    const primary = rows.find((r) => r.scopeKind === "ORG") ?? rows[0]!;
    const held = byUser.get(userId) ?? [];
    const lastActive = rows
      .map((r) => r.lastActiveAt)
      .filter((v): v is string => Boolean(v))
      .sort()
      .pop();
    return {
      kind: "user",
      key: `user:${userId}`,
      memberId: primary.id,
      user: primary.user,
      memberships: rows,
      bindings: held,
      teams: rows
        .filter((r) => r.scopeKind === "TEAM")
        .map((r) => ({ pk: r.scopeId, slug: teamNames.get(r.scopeId) ?? null })),
      lifecycle: primary.lifecycle,
      lastActiveAt: lastActive ?? null,
      joinedAt: primary.joinedAt ?? primary.createdAt,
      admin: held.some((b) => isAdminBinding(b, adminRoles)),
    };
  });

  const groups: GroupRow[] = [...byGroup.entries()].map(([externalId, held]) => ({
    kind: "group",
    key: `group:${externalId}`,
    externalId,
    bindings: held,
    admin: held.some((b) => isAdminBinding(b, adminRoles)),
  }));

  return { users, groups };
}

// ---------------------------------------------------------------------------
// The list declaration
// ---------------------------------------------------------------------------

/** The value the Mine view filters on: people who share a team with the viewer. */
export const MINE = "me";

export const ACTIVE_OPTIONS = [
  { value: "7d", label: "in the last 7 days" },
  { value: "30d", label: "in the last 30 days" },
  { value: "90d", label: "in the last 90 days" },
  { value: "stale", label: "not in 90 days" },
  { value: "never", label: "never" },
];

export const PEOPLE_LIST: ListDefinition = {
  id: "admin.access.people",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: [
        { value: "user", label: "user" },
        { value: "group", label: "group" },
      ],
    },
    // Role and team are typed (`role:org_admin`, `team:payments`); the hook
    // adds the org's roles and named teams as options.
    { key: "role", label: "Role" },
    { key: "team", label: "Team" },
    {
      key: "scope",
      label: "Scope",
      options: [
        { value: "ORG", label: "organization" },
        { value: "TEAM", label: "team" },
        { value: "PROJECT", label: "project" },
        { value: "APP", label: "app" },
      ],
    },
    { key: "active", label: "Last active", options: ACTIVE_OPTIONS },
    {
      key: "status",
      label: "Status",
      options: ["pending", "accepted", "expired", "revoked"].map((v) => ({ value: v, label: v })),
    },
  ],
  searchPlaceholder: "Search people, emails, groups…  role:org_admin  team:payments",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews(
    { mine: MINE },
    [
      {
        key: "invited",
        label: "Invited",
        filters: { kind: "invitation", status: "pending" },
        note: "Pending invitations. Add a status filter (status:accepted, expired, revoked) for the history.",
      },
      {
        key: "groups",
        label: "Groups",
        filters: { kind: "group" },
        note: "IdP groups that hold a role binding. The resolver does not match group bindings yet, so they grant nothing until it does; the full group list and members need the IdP sync.",
      },
      { key: "admins", label: "Admins", filters: { admin: "yes" } },
    ],
    { mineNote: "Mine means people who share a team with you." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** The definition with the org's roles and named teams as filter options. */
export function peopleList(roles: readonly AstroliftRole[], teamSlugs: readonly string[]) {
  return {
    ...PEOPLE_LIST,
    fields: PEOPLE_LIST.fields.map((f) =>
      f.key === "role" && roles.length > 0
        ? { ...f, options: roles.map((r) => ({ value: r.slug, label: r.slug })) }
        : f.key === "team" && teamSlugs.length > 0
          ? { ...f, options: teamSlugs.map((s) => ({ value: s, label: s })) }
          : f
    ),
  } satisfies ListDefinition;
}

/** Which walks a view needs, so only its queries run. */
export function viewNeeds(filters: Record<string, string>): {
  members: boolean;
  bindings: boolean;
  invitations: boolean;
} {
  if (filters.kind === "invitation") return { members: false, bindings: false, invitations: true };
  if (filters.kind === "group") return { members: false, bindings: true, invitations: false };
  return { members: true, bindings: true, invitations: false };
}

// ---------------------------------------------------------------------------
// Filter, sort, page
// ---------------------------------------------------------------------------

const DAY = 24 * 60 * 60 * 1000;

function activeMatches(lastActiveAt: string | null, want: string, now: number): boolean {
  if (want === "never") return !lastActiveAt;
  if (!lastActiveAt) return false;
  const ago = now - Date.parse(lastActiveAt);
  if (want === "stale") return ago > 90 * DAY;
  const days = Number.parseInt(want, 10);
  return Number.isFinite(days) && ago <= days * DAY;
}

function scopeKinds(row: UserRow | GroupRow): Set<string> {
  const kinds = new Set(row.bindings.map((b) => b.scopeKind as string));
  if (row.kind === "user") for (const m of row.memberships) kinds.add(m.scopeKind);
  return kinds;
}

function holdsRole(row: UserRow | GroupRow, role: string): boolean {
  const want = role.toLowerCase();
  return row.bindings.some(
    (b) => b.role.slug.toLowerCase() === want || b.role.name.toLowerCase() === want
  );
}

function searchMatches(row: PeopleRow, q: string): boolean {
  if (!q) return true;
  const hay =
    row.kind === "group"
      ? row.externalId
      : row.kind === "invitation"
        ? `${row.invitation.email} ${row.invitation.roleSlug ?? ""}`
        : `${row.user.username} ${row.user.email}`;
  return hay.toLowerCase().includes(q.toLowerCase());
}

interface SelectContext {
  filters: Record<string, string>;
  /** Applied to groups here; users and invitations were searched on the server. */
  q: string;
  sort: SortState[];
  page: number;
  pageSize: number;
  /** The viewer's username, for Mine. */
  me: string | null;
  now: number;
}

function matches(row: PeopleRow, ctx: SelectContext, myTeams: Set<string>): boolean {
  const f = ctx.filters;
  if (row.kind === "invitation") {
    if (f.kind !== "invitation") return false;
    if (f.status && row.invitation.status !== f.status) return false;
    if (f.role && (row.invitation.roleSlug ?? "").toLowerCase() !== f.role.toLowerCase())
      return false;
    return true;
  }
  if (f.kind === "invitation") return false;
  if (f.kind && row.kind !== f.kind) return false;
  if (row.kind === "group" && !searchMatches(row, ctx.q)) return false;
  if (f.admin === "yes" && !row.admin) return false;
  if (f.role && !holdsRole(row, f.role)) return false;
  if (f.scope && !scopeKinds(row).has(f.scope)) return false;
  if (f.team) {
    if (row.kind !== "user" || !row.teams.some((t) => t.slug === f.team || t.pk === f.team))
      return false;
  }
  if (f.mine === MINE) {
    if (row.kind !== "user" || !row.teams.some((t) => myTeams.has(t.pk))) return false;
  }
  if (f.active) {
    if (row.kind !== "user" || !activeMatches(row.lastActiveAt, f.active, ctx.now)) return false;
  }
  return true;
}

function nameOf(row: PeopleRow): string {
  return (
    row.kind === "user"
      ? row.user.username
      : row.kind === "group"
        ? row.externalId
        : row.invitation.email
  ).toLowerCase();
}

const SORT_VALUE: Record<string, (r: PeopleRow) => string | number> = {
  name: nameOf,
  // Never active sorts as the oldest.
  lastActive: (r) => (r.kind === "user" && r.lastActiveAt ? Date.parse(r.lastActiveAt) : 0),
  joined: (r) =>
    r.kind === "user"
      ? Date.parse(r.joinedAt) || 0
      : r.kind === "invitation"
        ? Date.parse(r.invitation.createdAt) || 0
        : 0,
  roles: (r) => (r.kind === "invitation" ? 0 : r.bindings.length),
};

function compare(a: PeopleRow, b: PeopleRow, sort: SortState[]): number {
  for (const s of sort) {
    const value = SORT_VALUE[s.key];
    if (!value) continue;
    const x = value(a);
    const y = value(b);
    if (x < y) return s.dir === "asc" ? -1 : 1;
    if (x > y) return s.dir === "asc" ? 1 : -1;
  }
  return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
}

/** The viewer's team pks: the TEAM rows of the user whose username is `me`. */
export function teamsOf(users: readonly UserRow[], me: string | null): Set<string> {
  const mine = me ? users.find((u) => u.user.username === me) : undefined;
  return new Set((mine?.teams ?? []).map((t) => t.pk));
}

/**
 * One numbered page: view filters and chips applied, sorted, sliced.
 * `filtered` is every match, for the CSV export; `totalCount` its size.
 */
export function selectPeople(
  rows: readonly PeopleRow[],
  ctx: SelectContext
): { rows: PeopleRow[]; totalCount: number; filtered: PeopleRow[] } {
  const users = rows.filter((r): r is UserRow => r.kind === "user");
  const myTeams = teamsOf(users, ctx.me);
  const filtered = rows
    .filter((r) => matches(r, ctx, myTeams))
    .sort((a, b) => compare(a, b, ctx.sort));
  const start = (Math.max(1, ctx.page) - 1) * ctx.pageSize;
  return {
    rows: filtered.slice(start, start + ctx.pageSize),
    totalCount: filtered.length,
    filtered,
  };
}
