/**
 * Admin › Access › People (access UX design 3.1): one list of principals.
 * Users are the list; IdP groups are its Groups view and a pending
 * invitation is a user in the Invited view, so invitation history is a
 * view, not a second table. Pure and server-safe: the declaration, the row
 * model, and the list state as each view's query variables.
 *
 * The server answers every view (#2153, #2126):
 *
 * - All, Mine, Admins and the user chips: `astroliftMembersPage` at ORG
 *   scope (one row per person), with the role, team, lifecycle, Mine
 *   (shares a team with the viewer), Admins (holds `org.manage_members` at
 *   org scope) and last-active filters, the column sorts and exact counts.
 *   Each row carries the teams the person is on.
 * - Groups: `astroliftPrincipalSearch` for GROUP, every IdP group the org
 *   knows (members' stored groups, group bindings and group mappings).
 * - Invited: `astroliftInvitationsPage` with its status and role filters.
 *
 * A page's roles come from one bindings read for the people on it.
 */
import type { Principal } from "@/components/access/access-model";
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";
import {
  type NumberedVariables,
  numberedVariables,
  one,
} from "@/components/screens/administration/access/identity-lists";
import type {
  AstroliftInvitation,
  AstroliftMember,
  AstroliftRole,
  AstroliftRoleBinding,
  AstroliftUser,
} from "@/graphql/identity/identity.types";

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

export interface PersonTeam {
  id: string;
  slug: string;
  name: string;
}

export interface UserRow {
  kind: "user";
  key: string;
  /** The ORG member row, which the detail page opens on. */
  memberId: string;
  user: AstroliftUser;
  /** Their bindings in the org: the page's roles read. */
  bindings: AstroliftRoleBinding[];
  teams: PersonTeam[];
  lifecycle: string;
  lastActiveAt: string | null;
  joinedAt: string;
  admin: boolean;
}

export interface GroupRow {
  kind: "group";
  key: string;
  externalId: string;
  /** Members who carry the group at their last sign-in. */
  memberCount: number;
  bindingsCount: number;
  mappingsCount: number;
}

export interface InvitationRow {
  kind: "invitation";
  key: string;
  invitation: AstroliftInvitation;
}

export type PeopleRow = UserRow | GroupRow | InvitationRow;

/** The permission that makes a holder an org admin, for the Admins view. */
export const ADMIN_PERMISSION = "org.manage_members";

/** The route param for a group's page: `group:<external id>`. */
export function groupParam(externalId: string): string {
  return `group:${externalId}`;
}

export function principalHref(row: UserRow | GroupRow): string {
  return row.kind === "user"
    ? `${PEOPLE_HREF}/${row.memberId}`
    : `${PEOPLE_HREF}/${encodeURIComponent(groupParam(row.externalId))}`;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

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
        detail: `IdP group · ${plural(row.memberCount, "member", "members")}`,
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
 * One row per person from their ORG member row, with their bindings (from
 * the page's bindings read) and whether one makes them an org admin.
 */
export function userRows(
  members: readonly AstroliftMember[],
  bindings: readonly AstroliftRoleBinding[],
  roles: readonly AstroliftRole[]
): UserRow[] {
  const adminRoles = new Set(
    roles.filter((r) => r.permissions.includes(ADMIN_PERMISSION)).map((r) => r.id)
  );
  const byUser = new Map<string, AstroliftRoleBinding[]>();
  for (const b of bindings) {
    if (b.user) byUser.set(b.user.id, [...(byUser.get(b.user.id) ?? []), b]);
  }
  return members.map((m) => {
    const held = byUser.get(m.user.id) ?? [];
    return {
      kind: "user",
      key: `user:${m.user.id}`,
      memberId: m.id,
      user: m.user,
      bindings: held,
      teams: (m.teams ?? []).map((t) => ({ id: t.id, slug: t.slug, name: t.name })),
      lifecycle: m.lifecycle,
      lastActiveAt: m.lastActiveAt ?? null,
      joinedAt: m.joinedAt ?? m.createdAt,
      admin: held.some((b) => b.scopeKind === "ORG" && adminRoles.has(b.role.id)),
    };
  });
}

/** The fields of an `astroliftPrincipalSearch` GROUP row the list reads. */
export interface GroupPrincipal {
  groupExternalId?: string | null;
  name: string;
  memberCount?: number | null;
  bindingsCount?: number | null;
  mappingsCount?: number | null;
}

export function groupRow(p: GroupPrincipal): GroupRow {
  const externalId = p.groupExternalId || p.name;
  return {
    kind: "group",
    key: `group:${externalId}`,
    externalId,
    memberCount: p.memberCount ?? 0,
    bindingsCount: p.bindingsCount ?? 0,
    mappingsCount: p.mappingsCount ?? 0,
  };
}

export function invitationRow(invitation: AstroliftInvitation): InvitationRow {
  return { kind: "invitation", key: `invitation:${invitation.id}`, invitation };
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
        { value: "invitation", label: "invitation" },
      ],
    },
    // Role and team are typed (`role:org_admin`, `team:payments`); the hook
    // adds the org's roles as options.
    { key: "role", label: "Role" },
    { key: "team", label: "Team" },
    {
      key: "lifecycle",
      label: "Lifecycle",
      options: ["active", "pending_invite", "pending_first_login", "suspended", "deactivated"].map(
        (v) => ({ value: v, label: v.replace(/_/g, " ") })
      ),
    },
    { key: "active", label: "Last active", options: ACTIVE_OPTIONS },
    {
      key: "status",
      label: "Invitation status",
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
        note: "IdP groups the organization knows: from members' sign-ins, group grants and group mappings. A group's grants and mappings apply to everyone the identity provider puts in it.",
      },
      { key: "admins", label: "Admins", filters: { admin: "yes" } },
    ],
    { mineNote: "Mine means people who share a team with you." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** The definition with the org's roles as the Role filter's options. */
export function peopleList(roles: readonly AstroliftRole[]): ListDefinition {
  return {
    ...PEOPLE_LIST,
    fields: PEOPLE_LIST.fields.map((f) =>
      f.key === "role" && roles.length > 0
        ? { ...f, options: roles.map((r) => ({ value: r.slug, label: r.slug })) }
        : f
    ),
  };
}

// ---------------------------------------------------------------------------
// Each view's query
// ---------------------------------------------------------------------------

export type PeopleSource = "members" | "groups" | "invitations";

/** Which query answers the view: only that one runs. */
export function sourceOf(filters: Record<string, string>): PeopleSource {
  if (filters.kind === "invitation") return "invitations";
  if (filters.kind === "group") return "groups";
  return "members";
}

export interface MembersFilter {
  scopeKind: string[];
  role?: string[];
  team?: string[];
  lifecycle?: string[];
  mine?: boolean;
  admin?: boolean;
  active?: string;
}

export interface InvitationsFilter {
  status?: string[];
  role?: string[];
}

/** The members sort keys the list's columns use; invitations spell two of them differently. */
const INVITATION_SORT: Record<string, string> = { name: "email", joined: "created" };

interface ListQuestion {
  q: string;
  filters: Record<string, string>;
  sort: SortState[];
  page: number;
  pageSize: number;
}

/** `astroliftMembersPage`: one ORG row per person, the chips as its filter. */
export function membersVariables(s: ListQuestion): NumberedVariables<MembersFilter> {
  const f = s.filters;
  return numberedVariables(s, {
    scopeKind: ["ORG"],
    role: one(f.role),
    team: one(f.team),
    lifecycle: one(f.lifecycle),
    mine: f.mine === MINE ? true : undefined,
    admin: f.admin === "yes" ? true : undefined,
    active: f.active || undefined,
  });
}

/** `astroliftInvitationsPage`: status and role, sorted by email or when sent. */
export function invitationsVariables(s: ListQuestion): NumberedVariables<InvitationsFilter> {
  const sort = s.sort
    .map((k) => ({ ...k, key: INVITATION_SORT[k.key] ?? k.key }))
    .filter((k) => ["email", "created", "expires", "status", "role"].includes(k.key));
  return numberedVariables(
    { ...s, sort: sort.length ? sort : [{ key: "created", dir: "desc" }] },
    { status: one(s.filters.status), role: one(s.filters.role) }
  );
}

/** `astroliftPrincipalSearch` for IdP groups; it orders by name and takes no sort. */
export function groupsVariables(s: ListQuestion) {
  return {
    search: s.q.trim() || null,
    filter: { kind: ["GROUP"] },
    page: Math.max(1, s.page),
    pageSize: s.pageSize,
  };
}
