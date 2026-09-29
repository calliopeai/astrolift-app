import type { ListStateController } from "@/components/list/list-state";
import type { AstroliftRole, AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import type { AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

import { type AccessRow, accessRows } from "./app-access-rows";
import { APP, LONG } from "./app-settings-members.fixtures";
import type { AppMembersScreenProps } from "./AppMembersScreen";

/**
 * Hand-typed fixtures for People with access (AppMembersScreen): role
 * bindings on checkout and its team shares, as the hook's rows.
 */

const noop = async () => {};

const ROLES = [
  { id: "role-1", slug: "app-admin", name: "App admin", scopeLevel: "APP" },
  { id: "role-2", slug: "app-deployer", name: "App deployer", scopeLevel: "APP" },
  { id: "role-3", slug: "app-viewer", name: "App viewer", scopeLevel: "APP" },
] as AstroliftRole[];

function binding(id: string, patch: Partial<AstroliftRoleBinding> = {}): AstroliftRoleBinding {
  return {
    id,
    user: { id: `u-${id}`, username: "leo", email: "leo@example.com" },
    groupExternalId: "",
    role: ROLES[0],
    scopeKind: "APP",
    scopeId: "app-1",
    sourceScopeLabel: "checkout",
    grantedAt: "2026-09-01T12:00:00Z",
    expiresAt: null,
    inherits: true,
    ...patch,
  } as AstroliftRoleBinding;
}

export const BINDINGS: AstroliftRoleBinding[] = [
  binding("rb-1"),
  binding("rb-2", {
    user: { id: "u-2", username: "eric", email: "eric@example.com" },
    role: ROLES[1],
    expiresAt: "2026-12-31T00:00:00Z",
  } as Partial<AstroliftRoleBinding>),
  binding("rb-3", {
    user: null,
    groupExternalId: "okta:platform-oncall",
    role: ROLES[2],
  } as Partial<AstroliftRoleBinding>),
];

function share(id: string, patch: Partial<AstroliftAppTeamAccess> = {}): AstroliftAppTeamAccess {
  return {
    id,
    appId: "app-1",
    appSlug: "checkout",
    teamId: `t-${id}`,
    teamSlug: "payments",
    teamName: "Payments",
    accessLevel: "owner",
    isHome: true,
    createdAt: "2026-06-01T12:00:00Z",
    updatedAt: "2026-06-01T12:00:00Z",
    ...patch,
  } as AstroliftAppTeamAccess;
}

export const SHARES: AstroliftAppTeamAccess[] = [
  share("s-1"),
  share("s-2", {
    teamSlug: "storefront",
    teamName: "Storefront",
    accessLevel: "deployer",
    isHome: false,
  }),
];

/** All's first page: team shares, then the bindings. */
export const ACCESS_ROWS: AccessRow[] = accessRows({
  view: "all",
  firstPage: true,
  bindings: BINDINGS,
  shares: SHARES,
  meId: "u-rb-1",
});

export const ACCESS_ROWS_LONG: AccessRow[] = accessRows({
  view: "all",
  firstPage: true,
  bindings: BINDINGS.map((b) => ({
    ...b,
    user: b.user
      ? { ...b.user, id: `u-${LONG}`, username: LONG, email: `${LONG}@example.com` }
      : null,
    groupExternalId: b.user ? "" : `azuread:${LONG}`,
    role: { ...b.role, slug: `${b.role.slug}-${LONG}`, name: LONG },
  })) as AstroliftRoleBinding[],
  shares: SHARES.map((s) => ({ ...s, teamSlug: LONG, teamName: LONG })),
  meId: null,
});

/** The screen's props around a list controller the story owns (useLocalListState). */
export function membersProps(
  list: ListStateController,
  overrides: Partial<AppMembersScreenProps> = {}
): AppMembersScreenProps {
  return {
    app: APP,
    loading: false,
    list,
    rows: ACCESS_ROWS,
    rowsLoading: false,
    stale: false,
    error: null,
    onRetry: () => {},
    totalCount: ACCESS_ROWS.length,
    nextCursor: null,
    removing: false,
    onRemove: noop,
    grantHref: "/administration/access/grant?scope=app%3Aapp-1&return=%2Fapps%2Fcheckout%2Faccess",
    checkHref: () => "/administration/permissions/diagnostics?on=app%3Aapp-1",
    slug: "checkout",
    ...overrides,
  };
}
