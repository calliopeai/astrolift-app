import { type ListDefinition, standardViews } from "@/components/list/list-state";

import { one, yesNo } from "../access/identity-lists";

const SCOPE_OPTIONS = [
  { value: "ORG", label: "organization" },
  { value: "TEAM", label: "team" },
  { value: "PROJECT", label: "project" },
  { value: "APP", label: "app" },
];

/**
 * Admin › Permissions › Roles (spec 44 §5.1) on `astroliftRolesPage`'s list
 * contract (#2153): views All · Mine (roles you created) · Built-in ·
 * Custom, a scope-level chip, the column sorts and numbered pages. The
 * server counts each role's holders in this org.
 */
export const ROLES_LIST: ListDefinition = {
  id: "admin.permissions.roles",
  fields: [
    { key: "scope", label: "Binds at", options: SCOPE_OPTIONS },
    {
      key: "builtin",
      label: "Built-in",
      options: [
        { value: "yes", label: "built-in" },
        { value: "no", label: "custom" },
      ],
    },
  ],
  searchPlaceholder: "Search roles by name or slug…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ createdBy: "me" }, [
    { key: "builtin", label: "Built-in", filters: { builtin: "yes" } },
    { key: "custom", label: "Custom", filters: { builtin: "no" } },
  ]),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export interface RolesListFilter {
  isSystem?: boolean;
  scopeLevel?: string[];
  createdBy?: string[];
}

export function rolesFilter(filters: Record<string, string>): RolesListFilter {
  return {
    isSystem: yesNo(filters.builtin),
    scopeLevel: one(filters.scope),
    createdBy: one(filters.createdBy),
  };
}

/** The binding filters Assignments and a role's Holders share. */
const BINDING_FIELDS = [
  { key: "scope", label: "Scope", options: SCOPE_OPTIONS },
  {
    key: "kind",
    label: "Held by",
    options: [
      { value: "user", label: "user" },
      { value: "group", label: "group" },
    ],
  },
];

/**
 * Admin › Permissions › Assignments: every role binding, on
 * `astroliftRoleBindingsPage`'s list contract. Mine is the viewer's own
 * bindings and those on the IdP groups they are in (`holder: me`).
 */
export const ASSIGNMENTS_LIST: ListDefinition = {
  id: "admin.permissions.assignments",
  fields: [
    // A role slug: `role:org_admin`.
    { key: "role", label: "Role" },
    ...BINDING_FIELDS,
    // A username or `group:<external id>`.
    { key: "holder", label: "Holder" },
  ],
  searchPlaceholder: "Search by user, group, or role…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ holder: "me" }, [
    { key: "groups", label: "Groups", filters: { kind: "group" } },
  ]),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export interface RoleBindingsListFilter {
  role?: string[];
  scopeKind?: string[];
  kind?: string[];
  holder?: string[];
}

export function bindingsFilter(filters: Record<string, string>): RoleBindingsListFilter {
  return {
    role: one(filters.role),
    scopeKind: one(filters.scope),
    kind: one(filters.kind),
    holder: one(filters.holder),
  };
}

/**
 * A role's Holders tab (design 3.5), embedded under the role's tabs: the
 * bindings of this role (`roleId`), with scope and holder-kind chips, the
 * column sorts and numbered pages with the exact count.
 */
export const ROLE_HOLDERS_LIST: ListDefinition = {
  id: "admin.permissions.role-holders",
  fields: BINDING_FIELDS,
  searchPlaceholder: "Search holders by user or group…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};
