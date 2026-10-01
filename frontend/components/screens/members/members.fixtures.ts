/**
 * Hand-typed fixtures for the Members screen and its sheets, typed
 * against each view's props so a story cannot drift from what the hooks
 * return.
 */
import type { ListStateController } from "@/components/list/list-state";
import type {
  AstroliftInvitation,
  AstroliftMember,
  AstroliftProject,
  AstroliftRole,
  AstroliftRoleBinding,
  AstroliftSearchableUser,
  AstroliftTeam,
  AstroliftUser,
} from "@/graphql/identity/identity.types";

import type { GrantRoleSheetProps } from "./GrantRoleDialog";
import type { InviteSheetProps } from "./InviteDialog";
import type { MembersScreenProps } from "./MembersScreen";
import {
  type GroupPrincipal,
  groupRow,
  invitationRow,
  type PeopleRow,
  sourceOf,
  userRows,
} from "./people-model";

const noop = () => {};
const resolved =
  <T>(value: T) =>
  () =>
    Promise.resolve(value);

const DAY = 24 * 60 * 60 * 1000;
/** An ISO timestamp `days` from now (negative for the past), so expiry badges read live. */
export const fromNow = (days: number) => new Date(Date.now() + days * DAY).toISOString();

/* ---- entities ---------------------------------------------------------- */

const ORG = {
  id: "org-7f3c2a10",
  slug: "acme",
  name: "Acme Corp",
};

const user = (id: string, username: string, email: string): AstroliftUser => ({
  id,
  username,
  email,
  isActive: true,
});

const ADA = user("1", "ada", "ada@example.com");
const GRACE = user("2", "grace", "grace@example.com");
const LINUS = user("3", "linus", "linus@example.com");
const MARGARET = user("4", "margaret", "margaret@example.com");
const LONG_USER = user(
  "5",
  "maximiliana.vandersloot-oyelaran",
  "maximiliana.vandersloot-oyelaran@subsidiary-holdings.example.com"
);

const role = (
  id: string,
  slug: string,
  name: string,
  patch: Partial<AstroliftRole> = {}
): AstroliftRole => ({
  id,
  slug,
  name,
  description: "",
  scopeLevel: "ORG",
  permissions: [],
  isSystem: true,
  ...patch,
});

export const ROLES: AstroliftRole[] = [
  role("r-owner", "org_owner", "Org Owner", { permissions: ["org.manage_members", "org.update"] }),
  role("r-admin", "org_admin", "Org Admin", { permissions: ["org.manage_members"] }),
  role("r-viewer", "viewer", "Viewer"),
  role("r-team", "team-lead", "Team Lead", { scopeLevel: "TEAM" }),
  role("r-release", "release-manager", "Release Manager", { scopeLevel: "PROJECT" }),
  role("r-app", "app-operator", "App Operator", { scopeLevel: "APP" }),
];
const [OWNER, ADMIN, VIEWER, TEAM_LEAD, RELEASE, APP_OPERATOR] = ROLES;

export const LONG_ROLE = role(
  "r-long",
  "regional-compliance-and-release-coordination-for-emea-subsidiaries",
  "Regional Compliance and Release Coordination for EMEA Subsidiaries (Interim)",
  { isSystem: false }
);

export const TEAMS: AstroliftTeam[] = [
  {
    id: "t-platform",
    slug: "platform",
    name: "Platform",
    organization: ORG,
    createdAt: "2026-01-10T09:00:00Z",
    updatedAt: "2026-01-10T09:00:00Z",
  },
  {
    id: "t-payments",
    slug: "payments",
    name: "Payments",
    organization: ORG,
    createdAt: "2026-02-03T09:00:00Z",
    updatedAt: "2026-02-03T09:00:00Z",
  },
];

export const PROJECTS: AstroliftProject[] = [
  {
    id: "p-checkout",
    slug: "checkout",
    name: "Checkout",
    organization: ORG,
    team: { id: "t-payments", slug: "payments", name: "Payments" },
    createdAt: "2026-02-04T09:00:00Z",
    updatedAt: "2026-02-04T09:00:00Z",
  },
];

const member = (
  id: string,
  u: AstroliftUser,
  patch: Partial<AstroliftMember> = {}
): AstroliftMember => ({
  id,
  user: u,
  scopeKind: "ORG",
  scopeId: "1",
  isActive: true,
  lifecycle: "active",
  createdAt: "2026-03-01T10:00:00Z",
  joinedAt: "2026-03-01T10:05:00Z",
  lastActiveAt: fromNow(-0.1),
  ...patch,
});

const PLATFORM = { id: "t-platform", slug: "platform", name: "Platform" };
const PAYMENTS = { id: "t-payments", slug: "payments", name: "Payments" };

/** ORG rows, one per person, as `astroliftMembersPage(filter: {scopeKind: ["ORG"]})` returns them. */
export const MEMBERS: AstroliftMember[] = [
  member("m-1", ADA, { teams: [PLATFORM] }),
  member("m-2", GRACE, { lastActiveAt: fromNow(-3), teams: [PAYMENTS] }),
  member("m-4", LINUS, { lastActiveAt: fromNow(-120), teams: [PLATFORM] }),
  member("m-5", MARGARET, { lastActiveAt: null, joinedAt: null, teams: [] }),
];

export const LONG_MEMBERS: AstroliftMember[] = [
  member("m-long", LONG_USER, {
    lastActiveAt: fromNow(-400),
    teams: [
      {
        id: "t-long",
        slug: "emea-subsidiary-quarter-end-freeze-coordination",
        name: "EMEA subsidiary quarter-end freeze coordination",
      },
      PLATFORM,
      PAYMENTS,
    ],
  }),
  member("m-anon", user("6", "anon-7c2f19ab", "7c2f19ab@anonymized.invalid"), {
    lifecycle: "anonymized",
    teams: [],
  }),
];

/** IdP groups as `astroliftPrincipalSearch(filter: {kind: ["GROUP"]})` returns them. */
export const GROUPS: GroupPrincipal[] = [
  {
    name: "okta:platform-admins",
    groupExternalId: "okta:platform-admins",
    memberCount: 3,
    bindingsCount: 1,
    mappingsCount: 0,
  },
  {
    name: "okta:release-managers",
    groupExternalId: "okta:release-managers",
    memberCount: 12,
    bindingsCount: 1,
    mappingsCount: 2,
  },
  {
    name: "okta:contractors",
    groupExternalId: "okta:contractors",
    memberCount: 7,
    bindingsCount: 0,
    mappingsCount: 0,
  },
];

export const LONG_GROUPS: GroupPrincipal[] = [
  {
    name: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
    groupExternalId: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
    memberCount: 1_284,
    bindingsCount: 14,
    mappingsCount: 3,
  },
];

const binding = (
  id: string,
  r: AstroliftRole,
  u: AstroliftUser | null,
  patch: Partial<AstroliftRoleBinding> = {}
): AstroliftRoleBinding => ({
  id,
  role: r,
  user: u,
  scopeKind: r.scopeLevel,
  scopeId: "1",
  sourceScopeLabel: "",
  groupExternalId: "",
  grantedAt: "2026-08-14T15:20:00Z",
  expiresAt: null,
  inherits: false,
  ...patch,
});

export const BINDINGS: AstroliftRoleBinding[] = [
  binding("b-1", OWNER, ADA),
  binding("b-2", ADMIN, GRACE, { sourceScopeLabel: "organization acme" }),
  binding("b-3", APP_OPERATOR, GRACE, {
    scopeId: "42",
    sourceScopeLabel: "app payments-api",
    grantedAt: "2026-09-02T09:05:00Z",
    expiresAt: fromNow(12),
  }),
  binding("b-4", TEAM_LEAD, LINUS, { scopeId: "14", sourceScopeLabel: "team platform" }),
  binding("b-5", RELEASE, null, {
    groupExternalId: "okta:release-managers",
    scopeId: "31",
    sourceScopeLabel: "project payments/checkout",
  }),
  binding("b-7", ADMIN, null, { groupExternalId: "okta:platform-admins" }),
  binding("b-6", VIEWER, MARGARET),
];

export const LONG_BINDINGS: AstroliftRoleBinding[] = [
  binding("b-long-1", LONG_ROLE, LONG_USER, {
    scopeKind: "PROJECT",
    scopeId: "977",
    sourceScopeLabel:
      "project emea/emea-subsidiary-quarter-end-freeze-coordination-and-release-readiness",
  }),
  binding("b-long-2", LONG_ROLE, null, {
    groupExternalId: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
  }),
];

const invitation = (
  id: string,
  email: string,
  patch: Partial<AstroliftInvitation> = {}
): AstroliftInvitation => ({
  id,
  email,
  status: "pending",
  scopeKind: "ORG",
  scopeId: ORG.id,
  roleSlug: "viewer",
  createdAt: "2026-09-20T12:00:00Z",
  expiresAt: fromNow(6.2),
  invitedByDisplayName: "Ada Lovelace",
  invitedByUsername: "ada",
  invitedByEmail: "ada@example.com",
  invitedByUserId: "1",
  invitedByAvatarUrl: null,
  ...patch,
});

export const INVITATIONS: AstroliftInvitation[] = [
  invitation("i-1", "katherine@example.com"),
  invitation("i-2", "dorothy@example.com", { roleSlug: null, expiresAt: fromNow(0.4) }),
  invitation("i-3", "mary@example.com", {
    roleSlug: "org_admin",
    invitedByDisplayName: null,
    invitedByUserId: null,
    invitedByEmail: null,
    invitedByUsername: "grace",
  }),
];

export const RESOLVED_INVITATIONS: AstroliftInvitation[] = [
  invitation("i-4", "hedy@example.com", {
    status: "accepted",
    acceptedAt: "2026-09-10T08:00:00Z",
    expiresAt: "2026-09-15T08:00:00Z",
  }),
  invitation("i-5", "barbara@example.com", {
    status: "revoked",
    expiresAt: "2026-09-01T08:00:00Z",
  }),
  invitation("i-6", "frances@example.com", {
    status: "expired",
    expiresAt: "2026-08-20T08:00:00Z",
    invitedByDisplayName: null,
    invitedByUsername: null,
  }),
];

export const LONG_INVITATIONS: AstroliftInvitation[] = [
  invitation(
    "i-long",
    "procurement-and-vendor-onboarding-shared-mailbox@emea-subsidiary-holdings.example.com",
    {
      roleSlug: LONG_ROLE.slug,
      invitedByDisplayName: "Maximiliana Vandersloot-Oyelaran (Regional Compliance Lead)",
      invitedByEmail: LONG_USER.email,
      invitedByUserId: LONG_USER.id,
    }
  ),
];

/* ---- props ------------------------------------------------------------- */

/** What the server answers a People story's view with: the rows as given, before paging. */
export interface PeopleData {
  members?: AstroliftMember[];
  /** The page's bindings, for the roles column. */
  bindings?: AstroliftRoleBinding[];
  groups?: GroupPrincipal[];
  invitations?: AstroliftInvitation[];
  roles?: AstroliftRole[];
}

/**
 * A stand-in for the server in stories: the rows of the query the view
 * reads, as given, one page of them. Filtering is the server's; a story
 * that shows a filtered view passes the rows that view returns.
 */
export function peopleRows(
  filters: Record<string, string>,
  {
    members = MEMBERS,
    bindings = BINDINGS,
    groups = GROUPS,
    invitations = [...INVITATIONS, ...RESOLVED_INVITATIONS],
    roles = ROLES,
  }: PeopleData = {}
): PeopleRow[] {
  switch (sourceOf(filters)) {
    case "groups":
      return groups.map(groupRow);
    case "invitations":
      return invitations
        .filter((i) => !filters.status || i.status === filters.status)
        .map(invitationRow);
    default:
      return userRows(members, bindings, roles);
  }
}

/** Everything but the list controller: the page `list` asks for, and the actions. */
export function membersProps(
  list: ListStateController,
  data: PeopleData = {},
  overrides: Partial<Omit<MembersScreenProps, "list">> = {}
): Omit<MembersScreenProps, "list"> {
  const rows = peopleRows(list.filters, data);
  const start = (list.state.page - 1) * list.state.pageSize;
  return {
    canManageMembers: true,
    rows: rows.slice(start, start + list.state.pageSize),
    totalCount: rows.length,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    onExportCsv: async () => {},
    exportingCsv: false,
    revokingInvite: false,
    deletingInvite: false,
    resendingInvite: false,
    onRevokeInvite: resolved(undefined),
    onResendInvite: resolved(undefined),
    onDeleteInvite: resolved(undefined),
    onAnonymize: resolved(true),
    ...overrides,
  };
}

export function grantRoleProps(overrides: Partial<GrantRoleSheetProps> = {}): GrantRoleSheetProps {
  return {
    organizationState: { loading: false, error: undefined },
    scopeReads: {
      TEAM: { loading: false, error: undefined, known: true },
      PROJECT: { loading: false, error: undefined, known: true },
    },
    onRetryScope: async () => {},
    open: true,
    onOpenChange: noop,
    roles: ROLES,
    initialUserId: null,
    initialUserLabel: null,
    org: {
      ...ORG,
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-01T00:00:00Z",
    } as GrantRoleSheetProps["org"],
    teams: TEAMS,
    projects: PROJECTS,
    granting: false,
    onGrant: resolved(true),
    ...overrides,
  };
}

export const MEMBER_MATCH: AstroliftSearchableUser = {
  matchKind: "MEMBER",
  email: "grace@example.com",
  displayLabel: "Grace Hopper",
  avatarUrl: "",
  userId: "2",
  invitationId: null,
  invitationStatus: null,
  expiresAt: null,
};

export const INVITATION_MATCH: AstroliftSearchableUser = {
  matchKind: "INVITATION",
  email: "katherine@example.com",
  displayLabel: "katherine@example.com",
  avatarUrl: "",
  userId: null,
  invitationId: "i-1",
  invitationStatus: "pending",
  expiresAt: fromNow(0.5),
};

export function inviteProps(overrides: Partial<InviteSheetProps> = {}): InviteSheetProps {
  return {
    open: true,
    onOpenChange: noop,
    email: "",
    setEmail: noop,
    roleSlug: "",
    setRoleSlug: noop,
    expiresInDays: "7",
    setExpiresInDays: noop,
    created: null,
    acceptUrl: "",
    searchActive: false,
    searchLoading: false,
    memberMatch: undefined,
    invitationMatch: undefined,
    blocksSubmit: false,
    grantableRoles: ROLES.filter((r) => r.scopeLevel === "ORG"),
    rolesLoading: false,
    noGrantableRoles: false,
    creating: false,
    revoking: false,
    onSubmit: resolved(undefined),
    onRevokeAndReinvite: resolved(undefined),
    onCopyAcceptUrl: noop,
    ...overrides,
  };
}
