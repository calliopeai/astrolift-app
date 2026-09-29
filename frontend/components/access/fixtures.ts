/**
 * Hand-typed fixtures for the shared access pieces, typed against their
 * props so a story cannot drift from what the hooks return. Emails use
 * example.com; long strings exercise the overflow rules (spec 44 §6, §8).
 */
import type { Principal, RoleRef, ScopeNode } from "./access-model";
import type { Comparison, Diagnosis } from "./AccessExplainer";
import type { GrantPreview } from "./GrantAccessFlow";
import type { PolicyShape } from "./policy-model";

export const LONG = "platform-engineering-release-captains-for-the-north-america-region-2026";
export const LONG_EMAIL =
  "a.very.long.name.that.keeps.going.for-the-overflow-rule@subdomain.example.com";

// ---------------------------------------------------------------------------
// Principals
// ---------------------------------------------------------------------------

export const DANA: Principal = {
  kind: "user",
  id: "42",
  name: "Dana Reyes",
  detail: "dana@example.com",
  href: "/administration/access/people/42",
};
export const SAM: Principal = {
  kind: "user",
  id: "57",
  name: "Sam Okafor",
  detail: "sam@example.com",
};
export const ENG_GROUP: Principal = {
  kind: "group",
  id: "okta:eng",
  name: "Engineering",
  detail: "Okta · 38 members",
  href: "/administration/access/people/group:okta:eng",
};
export const PAYMENTS_TEAM: Principal = {
  kind: "team",
  id: "payments",
  name: "Payments",
  detail: "6 members",
  href: "/administration/access/teams/payments",
};
export const CI_TOKEN: Principal = { kind: "token", id: "astl_7f3c", name: "ci-deployer" };
export const LONG_PRINCIPAL: Principal = {
  kind: "group",
  id: `azuread:${LONG}`,
  name: LONG,
  detail: LONG_EMAIL,
};

export const PRINCIPALS: Principal[] = [DANA, SAM, ENG_GROUP, PAYMENTS_TEAM, CI_TOKEN];

// ---------------------------------------------------------------------------
// Roles
// ---------------------------------------------------------------------------

export const VIEWER: RoleRef = {
  id: "r-viewer",
  slug: "app_viewer",
  name: "App viewer",
  description: "",
  scopeLevel: "APP",
  permissions: ["app.read", "app.read_logs", "app.read_metrics"],
  isSystem: true,
};

export const DEPLOYER: RoleRef = {
  id: "r-deployer",
  slug: "app_deployer",
  name: "App deployer",
  description: "Deploys and rolls back apps; reads their logs.",
  scopeLevel: "APP",
  permissions: ["app.read", "app.deploy", "app.rollback", "app.read_logs"],
  isSystem: true,
};

export const TEAM_DEV: RoleRef = {
  id: "r-team-dev",
  slug: "team_developer",
  name: "Team developer",
  description: "",
  scopeLevel: "TEAM",
  permissions: [
    "team.read",
    "project.read",
    "app.read",
    "app.deploy",
    "app.rollback",
    "secret.read",
    "secret.write",
    "secret.list",
    "agent.read",
    "agent.dispatch",
    "workflow.read",
    "workflow.trigger",
  ],
  isSystem: true,
};

/** A custom role duplicated from Team developer: two added, two removed. */
export const RELEASE_CAPTAIN: RoleRef = {
  id: "r-release",
  slug: "release-captain",
  name: "Release captain",
  description: "",
  scopeLevel: "TEAM",
  permissions: [
    "team.read",
    "project.read",
    "app.read",
    "app.deploy",
    "app.rollback",
    "app.approve_deploy",
    "secret.read",
    "secret.list",
    "agent.read",
    "workflow.read",
    "workflow.trigger",
    "audit_log.read",
  ],
  isSystem: false,
};

export const LONG_ROLE: RoleRef = {
  id: "r-long",
  slug: LONG,
  name: `Release captain for ${LONG}`,
  description: `Everything a release captain needs across ${LONG} and then some more words to wrap.`,
  scopeLevel: "PROJECT",
  permissions: ["app.read", "app.deploy", "app.rollback"],
  isSystem: false,
};

export const ROLES: RoleRef[] = [VIEWER, DEPLOYER, TEAM_DEV, RELEASE_CAPTAIN];

// ---------------------------------------------------------------------------
// Scopes
// ---------------------------------------------------------------------------

const app = (id: string, name: string): ScopeNode => ({ kind: "APP", id, name, slug: name });

export const SCOPE_TREE: ScopeNode[] = [
  {
    kind: "ORG",
    id: "org-acme",
    name: "Acme",
    slug: "acme",
    children: [
      {
        kind: "TEAM",
        id: "team-payments",
        name: "Payments",
        slug: "payments",
        children: [
          {
            kind: "PROJECT",
            id: "proj-storefront",
            name: "Storefront",
            slug: "storefront",
            children: [app("app-checkout", "checkout"), app("app-cart", "cart")],
          },
          {
            kind: "PROJECT",
            id: "proj-ledger",
            name: "Ledger",
            slug: "ledger",
            children: [app("app-ledger-api", "ledger-api"), app("app-support-bot", "support-bot")],
          },
        ],
      },
      {
        kind: "TEAM",
        id: "team-platform",
        name: "Platform",
        slug: "platform",
        children: [
          {
            kind: "PROJECT",
            id: "proj-infra",
            name: "Infra",
            slug: "infra",
            children: [app("app-edge", "edge-gateway")],
          },
        ],
      },
      app("app-orphan", "legacy-cron"),
    ],
  },
];

/** The same tree with its teams' children not loaded yet (lazy). */
export const LAZY_SCOPE_TREE: ScopeNode[] = [
  {
    ...SCOPE_TREE[0],
    children: SCOPE_TREE[0].children!.map((n) =>
      n.kind === "TEAM" ? { ...n, children: undefined, hasChildren: true } : n
    ),
  },
];

export const LONG_SCOPE_TREE: ScopeNode[] = [
  {
    kind: "ORG",
    id: "org-long",
    name: LONG,
    slug: LONG,
    children: [
      {
        kind: "TEAM",
        id: "team-long",
        name: LONG,
        slug: LONG,
        children: [
          {
            kind: "PROJECT",
            id: "proj-long",
            name: LONG,
            slug: LONG,
            children: [app("app-long", LONG)],
          },
        ],
      },
    ],
  },
];

export const CHECKOUT = { kind: "APP" as const, id: "app-checkout", name: "checkout" };

// ---------------------------------------------------------------------------
// Policies
// ---------------------------------------------------------------------------

export const AFTER_HOURS: PolicyShape = {
  effect: "DENY",
  actionPattern: "app.deploy",
  resource: { env: ["production"] },
  conditions: [
    {
      kind: "time_window",
      days: ["mon", "tue", "wed", "thu", "fri"],
      hours: ["09:00-18:00"],
      tz: "America/Los_Angeles",
    },
  ],
  actor: { groups: [], role: "" },
};

export const SECRETS_OFFICE: PolicyShape = {
  effect: "DENY",
  actionPattern: "secret.*",
  resource: {},
  conditions: [
    { kind: "ip_allowlist", cidrs: ["10.0.0.0/8", "192.168.0.0/16"] },
    { kind: "freshness", max_session_age_minutes: 15 },
  ],
  actor: { groups: ["okta:contractors"], role: "" },
};

export const EVERY_KIND: PolicyShape = {
  effect: "DENY",
  actionPattern: "*",
  resource: { app_slug: ["checkout"], project_slug: ["storefront"], region: ["us-west-2"] },
  conditions: [
    AFTER_HOURS.conditions[0],
    { kind: "ip_allowlist", cidrs: ["10.0.0.0/8"] },
    { kind: "approval_required", min_approvers: 2 },
    { kind: "env_match", env_in: ["staging", "preview"] },
    { kind: "device_assertion", required_factors: ["webauthn"] },
    { kind: "freshness", max_session_age_minutes: 5 },
    { kind: "custom", raw: { kind: "geo_fence", countries: ["US"] } },
  ],
  actor: { groups: ["okta:eng"], role: "team_developer" },
};

export const INVALID_POLICY: PolicyShape = {
  effect: "DENY",
  actionPattern: "app.deploy",
  resource: { env: [] },
  conditions: [
    { kind: "time_window", days: [], hours: ["9-5"], tz: "" },
    { kind: "ip_allowlist", cidrs: ["not-a-cidr"] },
  ],
  actor: { groups: [], role: "" },
};

export const LONG_POLICY: PolicyShape = {
  effect: "ALLOW",
  actionPattern: `app.${LONG}`,
  resource: { app_slug: [LONG], env: [LONG] },
  conditions: [{ kind: "env_match", env_in: [LONG, `${LONG}-2`] }],
  actor: { groups: [`azuread:${LONG}`], role: LONG },
};

// ---------------------------------------------------------------------------
// Check access
// ---------------------------------------------------------------------------

export const DIAGNOSIS_YES: Diagnosis = {
  username: "dana",
  permission: "app.deploy",
  granted: true,
  isSuperuser: false,
  steps: [
    { check: "is_active", result: true, detail: "User dana is active" },
    { check: "is_superuser", result: false, detail: "User dana is NOT a Django superuser" },
    {
      check: "permission_is_declared",
      result: true,
      detail: "'app.deploy' is a declared Astrolift permission",
    },
    { check: "has_active_organization", result: true, detail: "Active organization id: 1" },
    {
      check: "role_bindings_in_this_org",
      result: true,
      detail: "org_admin@ORG:1, team_developer@TEAM:14",
    },
    {
      check: "bindings_carrying_this_permission",
      result: true,
      detail: "org_admin@ORG:1, team_developer@TEAM:14",
    },
    { check: "resolver_verdict", result: true, detail: "role:org_admin@ORG:1" },
  ],
};

/** The #1717 shape: held on a team, asked at org scope. */
export const DIAGNOSIS_NO: Diagnosis = {
  username: "sam",
  permission: "app.deploy",
  granted: false,
  isSuperuser: false,
  steps: [
    { check: "is_active", result: true, detail: "User sam is active" },
    { check: "is_superuser", result: false, detail: "User sam is NOT a Django superuser" },
    {
      check: "permission_is_declared",
      result: true,
      detail: "'app.deploy' is a declared Astrolift permission",
    },
    { check: "has_active_organization", result: true, detail: "Active organization id: 1" },
    { check: "role_bindings_in_this_org", result: true, detail: "team_developer@TEAM:14" },
    { check: "bindings_carrying_this_permission", result: true, detail: "team_developer@TEAM:14" },
    {
      check: "resolver_verdict",
      result: false,
      detail:
        "no role binding grants this permission. Held at team_developer@TEAM:14, but this check was made at organization scope, an unqualified check resolves against the active tenant context.",
    },
  ],
};

export const DIAGNOSIS_SUPERUSER: Diagnosis = {
  username: "root",
  permission: "org.delete",
  granted: true,
  isSuperuser: true,
  steps: [
    { check: "is_active", result: true, detail: "User root is active" },
    {
      check: "is_superuser",
      result: true,
      detail: "User root is a Django superuser, the resolver short-circuits and grants everything",
    },
  ],
};

export const DIAGNOSIS_LONG: Diagnosis = {
  username: LONG,
  permission: `app.${LONG}`,
  granted: false,
  isSuperuser: false,
  steps: [
    {
      check: "permission_is_declared",
      result: false,
      detail: `'app.${LONG}' is not in the Astrolift permission catalog. Slugs are <resource>.<action>.`,
    },
    { check: "role_bindings_in_this_org", result: true, detail: `${LONG}@PROJECT:900719925474099` },
  ],
};

export const COMPARISON: Comparison = {
  userAUsername: "dana",
  userBUsername: "sam",
  onlyA: ["org.manage_members", "org.update", "team.create", "billing.read"],
  onlyB: ["agent.dispatch"],
  shared: ["app.read", "app.deploy", "app.rollback", "secret.read", "team.read", "project.read"],
};

// ---------------------------------------------------------------------------
// Grant preview
// ---------------------------------------------------------------------------

export const PREVIEW: GrantPreview = {
  gaining: [
    { principal: DANA, permissions: ["app.deploy", "app.rollback"] },
    { principal: SAM, permissions: ["app.deploy", "app.rollback", "app.read_logs"] },
  ],
  already: [
    {
      principal: { kind: "user", id: "61", name: "Lee Park", detail: "lee@example.com" },
      source: {
        via: { kind: "team", team: "payments" },
        inheritedFrom: { kind: "TEAM", id: "team-payments", name: "payments" },
      },
    },
  ],
};

export const PREVIEW_MANY: GrantPreview = {
  gaining: Array.from({ length: 14 }, (_, i) => ({
    principal: {
      kind: "user" as const,
      id: String(100 + i),
      name: `Engineer ${i + 1}`,
      detail: `engineer${i + 1}@example.com`,
    },
    permissions: ["app.deploy"],
  })),
  already: [],
  approximate:
    "Lists what the role carries, not what is new to each person: the backend has no grant preview.",
};
