/**
 * Hand-typed fixtures for the Administration › Permissions screen, typed
 * against each view's props so a story cannot drift from what the hooks
 * return.
 */
import type { AstroliftRole, AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { pageData } from "../access/fixtures";

import type { AssignmentsViewProps } from "./AssignmentsView";
import type { NewRoleScreenProps } from "./NewRoleScreen";
import type { PermissionsDiagnosticsViewProps } from "./PermissionsDiagnosticsView";
import { roleCatalog } from "./role-catalog";
import type { RoleDetailScreenProps } from "./RoleDetailScreen";
import type { RoleHoldersTabProps } from "./RoleHoldersTab";
import type { RolesViewProps } from "./RolesView";

const resolved =
  <T>(value: T) =>
  () =>
    Promise.resolve(value);

export const ALL_PERMISSIONS = [
  "api_token.create",
  "api_token.revoke",
  "app.create",
  "app.delete",
  "app.deploy",
  "app.read",
  "app.rollback",
  "audit_log.read",
  "billing.read",
  "cluster.manage",
  "cluster.read",
  "org.manage_members",
  "org.read",
  "policy.manage",
  "project.manage",
  "team.manage",
];

const role = (
  id: string,
  slug: string,
  name: string,
  permissions: string[],
  patch: Partial<AstroliftRole> = {}
): AstroliftRole => ({
  id,
  slug,
  name,
  description: "",
  scopeLevel: "ORG",
  permissions,
  isSystem: false,
  ...patch,
});

export const OWNER_ROLE = role("r-owner", "org_owner", "Org Owner", ALL_PERMISSIONS, {
  isSystem: true,
  description: "Full control of the organization.",
  bindingsCount: 2,
});
export const VIEWER_ROLE = role("r-viewer", "viewer", "Viewer", ["app.read", "cluster.read"], {
  isSystem: true,
  description: "Read-only access to apps and clusters.",
  bindingsCount: 41,
});
export const RELEASE_ROLE = role(
  "r-release",
  "release-manager",
  "Release Manager",
  ["app.deploy", "app.read", "app.rollback"],
  { scopeLevel: "PROJECT", description: "Ships and rolls back releases.", bindingsCount: 3 }
);
export const TEAM_ROLE = role("r-team", "team-lead", "Team Lead", ["team.manage", "app.read"], {
  scopeLevel: "TEAM",
  bindingsCount: 6,
});
export const APP_ROLE = role("r-app", "app-operator", "App Operator", ["app.deploy"], {
  scopeLevel: "APP",
  bindingsCount: 0,
});

export const ROLES: AstroliftRole[] = [OWNER_ROLE, VIEWER_ROLE, RELEASE_ROLE, TEAM_ROLE, APP_ROLE];

export const LONG_ROLE = role(
  "r-long",
  "regional-compliance-and-release-coordination-for-emea-subsidiaries",
  "Regional Compliance and Release Coordination for EMEA Subsidiaries (Interim)",
  [
    ...ALL_PERMISSIONS,
    "deploy_token.create_with_an_unreasonably_long_verb_name",
    "unprefixed_permission_without_resource",
  ],
  {
    scopeLevel: "PROJECT",
    description:
      "Temporary role covering the quarter-end freeze for every EMEA subsidiary project; review and prune before the next audit window closes.",
  }
);

/** Everything but the list controller, which a story makes with useLocalListState. */
export function rolesProps(
  overrides: Partial<Omit<RolesViewProps, "list">> = {}
): Omit<RolesViewProps, "list"> {
  return { page: pageData(ROLES), ...overrides };
}

/** A custom role duplicated from Viewer and widened: the diff has something to show. */
export const AUDITOR_ROLE = role(
  "r-auditor",
  "auditor",
  "Auditor",
  ["app.read", "audit_log.read", "billing.read", "cluster.read"],
  {
    description: "Reads everything an audit needs, changes nothing.",
    bindingsCount: 4,
    duplicatedFrom: {
      id: "r-viewer",
      slug: "viewer",
      name: "Viewer",
      isSystem: true,
      permissions: ["app.read", "cluster.read"],
      deleted: false,
    },
  }
);

export const DETAIL_ROLES: AstroliftRole[] = [...ROLES, AUDITOR_ROLE];
export const CATALOG = roleCatalog(DETAIL_ROLES);

export function roleDetailProps(
  role: AstroliftRole | null = RELEASE_ROLE,
  overrides: Partial<Omit<RoleDetailScreenProps, "tab">> = {}
): Omit<RoleDetailScreenProps, "tab"> {
  return {
    id: role?.id ?? "r-missing",
    role,
    roles: DETAIL_ROLES,
    catalog: CATALOG,
    loading: false,
    error: null,
    onRetry: () => {},
    canManage: true,
    saving: false,
    savePermissions: resolved(null),
    saveSettings: resolved(null),
    ...overrides,
  };
}

export function newRoleProps(overrides: Partial<NewRoleScreenProps> = {}): NewRoleScreenProps {
  return {
    roles: DETAIL_ROLES,
    from: null,
    catalog: CATALOG,
    loading: false,
    creating: false,
    onCreate: resolved(null),
    onCancel: () => {},
    ...overrides,
  };
}

const user = (id: string, username: string, email: string) => ({
  id,
  username,
  email,
  isActive: true,
});

const binding = (
  id: string,
  r: AstroliftRole,
  patch: Partial<AstroliftRoleBinding> = {}
): AstroliftRoleBinding => ({
  id,
  role: r,
  scopeKind: r.scopeLevel,
  scopeId: "org-7f3c2a10",
  sourceScopeLabel: "",
  groupExternalId: "",
  grantedAt: "2026-08-14T15:20:00Z",
  expiresAt: null,
  inherits: false,
  user: user("u-ada", "ada", "ada@example.com"),
  ...patch,
});

export const BINDINGS: AstroliftRoleBinding[] = [
  binding("b-1", OWNER_ROLE),
  binding("b-2", RELEASE_ROLE, {
    user: user("u-grace", "grace", "grace@example.com"),
    sourceScopeLabel: "project: checkout",
    grantedAt: "2026-09-02T09:05:00Z",
    expiresAt: "2026-12-31T23:59:00Z",
  }),
  binding("b-3", TEAM_ROLE, {
    user: null,
    groupExternalId: "okta:platform-engineers",
    sourceScopeLabel: "team: platform",
  }),
  binding("b-4", APP_ROLE, {
    user: user("u-linus", "linus", "linus@example.com"),
    sourceScopeLabel: "app: payments-api",
  }),
];

export const LONG_BINDINGS: AstroliftRoleBinding[] = [
  binding("b-long-1", LONG_ROLE, {
    user: user(
      "u-long",
      "maximiliana.vandersloot-oyelaran",
      "maximiliana.vandersloot-oyelaran@subsidiary-holdings.example.com"
    ),
    sourceScopeLabel:
      "project: emea-subsidiary-quarter-end-freeze-coordination-and-release-readiness",
    expiresAt: "2026-12-31T23:59:00Z",
  }),
  binding("b-long-2", LONG_ROLE, {
    user: null,
    groupExternalId: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
  }),
];

/** Everything but the list controller, which a story makes with useLocalListState. */
export function assignmentsProps(
  overrides: Partial<Omit<AssignmentsViewProps, "list">> = {}
): Omit<AssignmentsViewProps, "list"> {
  return {
    page: pageData(BINDINGS),
    canManage: true,
    rolesLoading: false,
    revoking: false,
    bulkRevoking: false,
    onRevoke: resolved(undefined),
    onBulkRevoke: resolved(true),
    exportingCsv: false,
    onExportCsv: resolved(undefined),
    ...overrides,
  };
}

export const ME: NonNullable<PermissionsDiagnosticsViewProps["me"]> = {
  kind: "user",
  id: "u-ada",
  name: "ada",
};

const noSearch = () => ({ query: "", setQuery: () => {}, results: [] });

/**
 * Check access asked nothing yet, as the hook returns it for the viewer.
 * Stories pass the answer, the scope tree and the picks they need.
 */
export function diagnosticsProps(
  overrides: Partial<PermissionsDiagnosticsViewProps> = {}
): PermissionsDiagnosticsViewProps {
  return {
    me: ME,
    meLoading: false,
    comparing: false,
    onCompareToggle: () => {},
    who: ME,
    whoSearch: noSearch(),
    onWhoChange: () => {},
    permission: null,
    onPermissionChange: () => {},
    scope: null,
    onScopeChange: () => {},
    scopeTree: { roots: [], loading: false, error: null, onRetry: () => {} },
    explainer: { diagnosis: null, loading: false, error: null, onRetry: () => {} },
    bindingHref: () => "/administration/permissions/assignments",
    other: null,
    otherSearch: noSearch(),
    onOtherChange: () => {},
    compare: { comparison: null, loading: false, error: null, onRetry: () => {} },
    ...overrides,
  };
}

/** A role's holders: two users and an IdP group's mapping. */
export const RELEASE_HOLDERS: AstroliftRoleBinding[] = [
  BINDINGS[1],
  binding("b-5", RELEASE_ROLE, {
    user: null,
    groupExternalId: "okta:release-captains",
    sourceScopeLabel: "project: storefront",
  }),
  binding("b-6", RELEASE_ROLE, {
    user: user("u-dana", "dana", "dana@example.com"),
    sourceScopeLabel: "project: checkout",
  }),
];

/** Everything but the list controller, which a story makes with useLocalListState. */
export function holdersProps(
  overrides: Partial<Omit<RoleHoldersTabProps, "list">> = {}
): Omit<RoleHoldersTabProps, "list"> {
  return {
    page: pageData(RELEASE_HOLDERS, { totalCount: null }),
    canManage: true,
    revoking: false,
    onRevoke: resolved(undefined),
    roleName: RELEASE_ROLE.name,
    grantHref: "/administration/access/grant?role=r-release",
    ...overrides,
  };
}
