/**
 * Hand-typed fixtures for the Members screen and its sheets, typed
 * against each view's props so a story cannot drift from what the hooks
 * return.
 */
import { pageData } from "@/components/screens/administration/access/fixtures";
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
  role("r-owner", "org_owner", "Org Owner"),
  role("r-admin", "org_admin", "Org Admin"),
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
  scopeId: ORG.id,
  isActive: true,
  lifecycle: "active",
  createdAt: "2026-03-01T10:00:00Z",
  joinedAt: "2026-03-01T10:05:00Z",
  lastActiveAt: fromNow(-0.1),
  ...patch,
});

export const MEMBERS: AstroliftMember[] = [
  member("m-1", ADA),
  member("m-2", GRACE, { lastActiveAt: fromNow(-3) }),
  // A second, APP-scope row for grace: collapses into one People row.
  member("m-3", GRACE, { scopeKind: "APP", scopeId: "app-42", lastActiveAt: fromNow(-3) }),
  member("m-4", LINUS, { scopeKind: "TEAM", scopeId: "t-platform", lastActiveAt: fromNow(-120) }),
  member("m-5", MARGARET, {
    scopeKind: "PROJECT",
    scopeId: "p-checkout",
    lastActiveAt: null,
    joinedAt: null,
  }),
];

export const LONG_MEMBERS: AstroliftMember[] = [
  member("m-long", LONG_USER, {
    scopeKind: "PROJECT",
    scopeId: "p-missing",
    lastActiveAt: fromNow(-400),
  }),
  member("m-anon", user("6", "anon-7c2f19ab", "7c2f19ab@anonymized.invalid"), {
    lifecycle: "anonymized",
  }),
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
  scopeId: ORG.id,
  sourceScopeLabel: "",
  groupExternalId: "",
  grantedAt: "2026-08-14T15:20:00Z",
  expiresAt: null,
  inherits: false,
  ...patch,
});

export const BINDINGS: AstroliftRoleBinding[] = [
  binding("b-1", OWNER, ADA),
  binding("b-2", ADMIN, GRACE, { sourceScopeLabel: "organization: acme" }),
  binding("b-3", APP_OPERATOR, GRACE, {
    scopeId: "app-42",
    sourceScopeLabel: "app: payments-api",
    grantedAt: "2026-09-02T09:05:00Z",
  }),
  binding("b-4", TEAM_LEAD, LINUS, { scopeId: "t-platform", sourceScopeLabel: "team: platform" }),
  binding("b-5", RELEASE, null, {
    groupExternalId: "okta:release-managers",
    scopeId: "p-checkout",
    sourceScopeLabel: "project: payments/checkout",
  }),
  binding("b-6", VIEWER, MARGARET),
];

export const LONG_BINDINGS: AstroliftRoleBinding[] = [
  binding("b-long-1", LONG_ROLE, LONG_USER, {
    scopeKind: "PROJECT",
    scopeId: "p-missing",
    sourceScopeLabel:
      "project: emea-subsidiary-quarter-end-freeze-coordination-and-release-readiness",
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

/** Everything but the list controller, which a story makes with useLocalListState. */
export function membersProps(
  overrides: Partial<Omit<MembersScreenProps, "list">> = {}
): Omit<MembersScreenProps, "list"> {
  return {
    canManageMembers: true,
    people: pageData(MEMBERS, { totalCount: 140, nextCursor: "c2" }),
    invitations: pageData(INVITATIONS),
    bindings: pageData(BINDINGS),
    bindingIndexRows: BINDINGS,
    teams: TEAMS,
    projects: PROJECTS,
    roles: ROLES,
    rolesLoading: false,
    revoking: false,
    bulkRevoking: false,
    revokingInvite: false,
    deletingInvite: false,
    resendingInvite: false,
    onRevokeInvite: resolved(undefined),
    onResendInvite: resolved(undefined),
    onDeleteInvite: resolved(undefined),
    onRevokeBinding: resolved(undefined),
    onBulkRevoke: resolved(true),
    onAnonymize: resolved(true),
    ...overrides,
  };
}

export function grantRoleProps(overrides: Partial<GrantRoleSheetProps> = {}): GrantRoleSheetProps {
  return {
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
