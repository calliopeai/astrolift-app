/**
 * Story fixtures for the principal pages (a person, a group, a team) and
 * their tabs, typed against each panel's props and selected the way the
 * hooks select, so a story cannot drift from them.
 */
import type { ListStateController } from "@/components/list/list-state";
import type {
  AstroliftMember,
  AstroliftRole,
  AstroliftRoleBinding,
} from "@/graphql/identity/identity.types";
import type { AstroliftAuditEvent } from "@/graphql/operations/operations.types";

import { MEMBER, ROLE_BINDINGS } from "./fixtures";
import type { PersonActivityPanelProps } from "./PersonActivityPanel";
import type { PersonTeamsPanelProps } from "./PersonTeamsPanel";
import { buildAccessRows, selectAccess, summarizeAccess } from "./principal-access";
import type { PrincipalAccessPanelProps } from "./PrincipalAccessPanel";
import { selectMemberships, type TeamMembershipRow } from "./use-person-teams";

const noop = () => {};

const role = (
  id: string,
  slug: string,
  name: string,
  scopeLevel: AstroliftRole["scopeLevel"],
  permissions: string[],
  description = ""
): AstroliftRole => ({ id, slug, name, scopeLevel, permissions, description, isSystem: true });

/** The catalog roles the fixture bindings point at (their ids are `role-<n>`). */
export const ACCESS_ROLES: AstroliftRole[] = [
  role("role-1", "org_viewer", "org_viewer", "ORG", ["app.read", "agent.read", "org.read"]),
  role("role-2", "team_developer", "team_developer", "TEAM", [
    "app.read",
    "app.create",
    "app.update",
    "app.deploy",
  ]),
  role(
    "role-3",
    "project_admin",
    "project_admin",
    "PROJECT",
    ["app.read", "app.update", "app.deploy", "app.rollback", "secret.read"],
    "Deploys and rolls back the project's apps; reads their secrets."
  ),
  role("role-4", "app_admin", "app_admin", "APP", ["app.read", "app.update", "app.deploy"]),
];

/** A team grant and an org grant of the same role, so the team one reads as covered. */
export const COVERED_BINDINGS: AstroliftRoleBinding[] = [
  ...ROLE_BINDINGS,
  {
    ...ROLE_BINDINGS[1]!,
    id: "rb-org-dev",
    scopeKind: "ORG",
    scopeId: "1",
    sourceScopeLabel: "organization acme",
    expiresAt: "2026-12-31T00:00:00Z",
  },
];

/** Everything but the list controller: the page `list` selects, the summary, the actions. */
export function accessProps(
  list: ListStateController,
  bindings: AstroliftRoleBinding[] = ROLE_BINDINGS,
  overrides: Partial<Omit<PrincipalAccessPanelProps, "list">> = {}
): Omit<PrincipalAccessPanelProps, "list"> {
  const rows = buildAccessRows(bindings, ACCESS_ROLES);
  const selected = selectAccess(rows, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return {
    rows: selected.rows,
    totalCount: selected.totalCount,
    summary: summarizeAccess(rows),
    loading: false,
    error: null,
    onRetry: noop,
    canManage: true,
    onRevoke: () => Promise.resolve(),
    onBulkRevoke: () => Promise.resolve(true),
    holderLabel: (b) => b.user?.username ?? b.groupExternalId,
    grantHref: "/administration/access/grant",
    ...overrides,
  };
}

const membership = (
  id: string,
  pk: string,
  patch: Partial<AstroliftMember> = {}
): AstroliftMember => ({
  ...MEMBER,
  id,
  scopeKind: "TEAM",
  scopeId: pk,
  ...patch,
});

export const MEMBERSHIPS: TeamMembershipRow[] = [
  { member: membership("m-t1", "14"), pk: "14", slug: "platform" },
  { member: membership("m-t2", "21", { lifecycle: "invited" }), pk: "21", slug: "payments" },
  // On the team with no binding there, so nothing names it yet.
  { member: membership("m-t3", "38"), pk: "38", slug: null },
];

export function teamsProps(
  list: ListStateController,
  rows: TeamMembershipRow[] = MEMBERSHIPS,
  overrides: Partial<Omit<PersonTeamsPanelProps, "list">> = {}
): Omit<PersonTeamsPanelProps, "list"> {
  const selected = selectMemberships(rows, {
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return {
    rows: selected.rows,
    totalCount: selected.totalCount,
    loading: false,
    error: null,
    onRetry: noop,
    ...overrides,
  };
}

const event = (
  id: string,
  action: string,
  occurredAt: string,
  patch: Partial<AstroliftAuditEvent> = {}
): AstroliftAuditEvent => ({
  id,
  organizationId: "org-1",
  occurredAt,
  actorKind: "user",
  actorId: MEMBER.user.id,
  actorDisplay: MEMBER.user.username,
  action,
  decision: "ALLOW",
  targetKind: "app",
  targetId: "42",
  targetSlug: "checkout-api",
  requestId: `req-${id}`,
  data: {},
  before: null,
  after: null,
  ...patch,
});

export const EVENTS: AstroliftAuditEvent[] = [
  event("e1", "app.deploy", "2026-09-28T09:12:00Z"),
  event("e2", "secret.read", "2026-09-28T08:40:00Z", {
    targetKind: "secret",
    targetSlug: "stripe-key",
  }),
  event("e3", "app.delete", "2026-09-27T17:05:00Z", { decision: "DENY" }),
  event("e4", "rolebinding.grant", "2026-09-26T11:30:00Z", {
    targetKind: "role_binding",
    targetSlug: "",
    targetId: "rb-9c2d41e0-7a13-4b6e-9f0a-1d2e3f4a5b6c-a-very-long-identifier",
  }),
];

export function activityProps(
  overrides: Partial<PersonActivityPanelProps> = {}
): PersonActivityPanelProps {
  return {
    allowed: true,
    items: EVENTS,
    loading: false,
    error: null,
    hasMore: true,
    loadingMore: false,
    onLoadMore: noop,
    onRetry: noop,
    auditHref: `/administration/audit?actor=${MEMBER.user.id}`,
    ...overrides,
  };
}
