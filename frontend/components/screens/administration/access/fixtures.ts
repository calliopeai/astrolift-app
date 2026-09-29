/**
 * Hand-typed story fixtures for the administration access screens, typed
 * against each screen's props so a story cannot drift from its hook.
 */
import { CONDITION_CATALOG, SIMULATION } from "@/components/access/fixtures";
import type {
  AstroliftMember,
  AstroliftPolicy,
  AstroliftRoleBinding,
} from "@/graphql/identity/identity.types";

import type { MemberDetailProps } from "./MemberDetail";
import type { PoliciesScreenProps } from "./PoliciesScreen";
import type { PolicyEditorScreenProps } from "./PolicyEditorScreen";
import type { ListPageData } from "./use-list-page-query";

const USER = {
  id: "u-2f81",
  username: "ada.lovelace",
  email: "ada@example.com",
  isActive: true,
};

export const MEMBER: AstroliftMember = {
  id: "9c2d41e0-7a13-4b6e-9f0a-1d2e3f4a5b6c",
  user: USER,
  lifecycle: "active",
  isActive: true,
  scopeKind: "ORG",
  scopeId: "org-7f3c2a91",
  joinedAt: "2026-06-02T14:10:00Z",
  lastActiveAt: "2026-09-27T18:42:00Z",
  lastSeenAt: "2026-09-28T08:05:00Z",
  createdAt: "2026-06-01T09:00:00Z",
  deletedAt: null,
};

const role = (id: string, name: string) => ({
  id: `role-${id}`,
  name,
  slug: name,
  description: "",
  isSystem: true,
  permissions: [],
  scopeLevel: "ORG" as const,
});

const binding = (
  id: string,
  name: string,
  scopeKind: AstroliftRoleBinding["scopeKind"],
  sourceScopeLabel: string,
  inherits = false
): AstroliftRoleBinding => ({
  id: `rb-${id}`,
  role: role(id, name),
  scopeKind,
  scopeId: `${scopeKind.toLowerCase()}-${id}`,
  sourceScopeLabel,
  inherits,
  grantedAt: "2026-09-01T00:00:00Z",
  expiresAt: null,
  groupExternalId: "",
  user: USER,
});

// Labels in the server's form (`_resolve_source_scope_labels`), which the
// Access tab parses to name and link each scope.
export const ROLE_BINDINGS: AstroliftRoleBinding[] = [
  binding("1", "org_viewer", "ORG", "organization acme"),
  binding("2", "team_developer", "TEAM", "team platform"),
  binding("3", "project_admin", "PROJECT", "project platform/checkout-service", true),
  binding("4", "app_admin", "APP", "app checkout-api"),
  binding("5", "app_admin", "APP", "app checkout-worker"),
];

const LONG = "a-very-long-identifier-that-keeps-going-well-past-any-sensible-column-width";

export const LONG_ROLE_BINDINGS: AstroliftRoleBinding[] = [
  binding("6", `org_${LONG}`, "ORG", `organization acme-international-holdings-${LONG}`),
  binding("7", `project_${LONG}`, "PROJECT", `project platform/checkout-${LONG}`, true),
];

/** The page frame's props; a story adds the tab and its body. */
export const MEMBER_DETAIL: Omit<MemberDetailProps, "tab" | "children"> = {
  id: MEMBER.id,
  member: MEMBER,
  canManage: true,
  loading: false,
  error: null,
  onRetry: () => {},
};

export const LONG_MEMBER: AstroliftMember = {
  ...MEMBER,
  lifecycle: "suspended",
  isActive: false,
  user: {
    ...USER,
    username: `ada.augusta.king.countess.of.lovelace.${LONG}`,
    email: `ada.augusta.king.countess.of.lovelace.${LONG}@example.com`,
    isActive: false,
  },
  scopeKind: "PROJECT",
  scopeId: `project-${LONG}`,
};

// The JSON scalar is typed as an object, but the server sends conditions as an
// array; the screen counts them with Array.isArray.
const conditions = (rows: Record<string, unknown>[]) =>
  rows as unknown as AstroliftPolicy["conditions"];

export const POLICIES: AstroliftPolicy[] = [
  {
    id: "pol-1",
    name: "No prod deploys after hours",
    slug: "no-prod-deploys-after-hours",
    description: "",
    scopeLevel: "ORG",
    scopeId: null,
    effect: "DENY",
    actionPattern: "app.deploy",
    actorPattern: {},
    resourcePattern: {},
    conditions: conditions([{ kind: "time_window", days: ["mon", "tue"], hours: ["09:00-18:00"] }]),
    createdByUsername: "ada.lovelace",
    updatedByUsername: null,
    createdAt: "2026-08-14T10:00:00Z",
    updatedAt: "2026-08-14T10:00:00Z",
    deletedAt: null,
    version: 1,
  },
  {
    id: "pol-2",
    name: "Office network only",
    slug: "office-network-only",
    description: "",
    scopeLevel: "TEAM",
    scopeId: "team-platform",
    effect: "DENY",
    actionPattern: "*",
    actorPattern: {},
    resourcePattern: {},
    conditions: conditions([
      { kind: "ip_allowlist", cidrs: ["10.0.0.0/8"] },
      { kind: "freshness", maxAgeSeconds: 900 },
    ]),
    createdByUsername: null,
    updatedByUsername: null,
    createdAt: "2026-09-02T12:30:00Z",
    updatedAt: "2026-09-02T12:30:00Z",
    deletedAt: null,
    version: 2,
  },
  {
    id: "pol-3",
    name: "Staging break-glass",
    slug: "staging-break-glass",
    description: "",
    scopeLevel: "PROJECT",
    scopeId: "project-checkout",
    effect: "ALLOW",
    actionPattern: "app.restart",
    actorPattern: {},
    resourcePattern: {},
    conditions: conditions([]),
    createdByUsername: "grace.hopper",
    updatedByUsername: null,
    createdAt: "2026-09-20T08:15:00Z",
    updatedAt: "2026-09-20T08:15:00Z",
    deletedAt: null,
    version: 1,
  },
];

export const LONG_POLICIES: AstroliftPolicy[] = [
  {
    ...POLICIES[0],
    id: "pol-long",
    name: `Deny every production deploy outside the change window ${LONG}`,
    slug: `deny-every-production-deploy-${LONG}`,
    actionPattern: `app.deploy.${LONG}`,
    createdByUsername: `ada.augusta.king.countess.of.lovelace.${LONG}`,
  },
];

const noop = () => {};
const noopAsync = async () => {};

/** One page of a list, as useListPageQuery and useNumberedListQuery return it. */
export function pageData<TRow>(
  rows: TRow[],
  patch: Partial<ListPageData<TRow>> = {}
): ListPageData<TRow> {
  return {
    rows,
    totalCount: rows.length,
    nextCursor: null,
    loading: false,
    stale: false,
    error: null,
    refetch: noop,
    ...patch,
  };
}

/** Everything but the list controller, which a story makes with useLocalListState. */
export function policiesProps(
  page: Partial<ListPageData<AstroliftPolicy>> = {},
  overrides: Partial<Omit<PoliciesScreenProps, "list">> = {}
): Omit<PoliciesScreenProps, "list"> {
  return {
    page: pageData(POLICIES, page),
    canManage: true,
    deleting: false,
    deletePolicy: noopAsync,
    ...overrides,
  };
}

/** The editor on New: nothing to load. */
export const NEW_POLICY: PolicyEditorScreenProps = {
  mode: "create",
  id: null,
  policy: null,
  loading: false,
  error: null,
  onRetry: noop,
  canManage: true,
  catalog: { conditions: CONDITION_CATALOG, loading: false, error: null },
  simulate: async () => SIMULATION,
  saving: false,
  onSave: async () => null,
  onCancel: noop,
};

/** The editor on an existing policy. */
export function editPolicyProps(
  overrides: Partial<PolicyEditorScreenProps> = {}
): PolicyEditorScreenProps {
  return { ...NEW_POLICY, mode: "edit", id: POLICIES[0].id, policy: POLICIES[0], ...overrides };
}
