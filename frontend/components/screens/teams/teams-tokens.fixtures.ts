import type { AstroliftApiTokenScopeCatalog } from "@/graphql/__generated__/schema";
import type {
  AstroliftApiToken,
  AstroliftApiTokenPlaintext,
  AstroliftTeam,
} from "@/graphql/identity/identity.types";

import type { ScopePickerProps } from "../tokens/ScopePicker";
import type { TokenDetailScreenProps } from "../tokens/TokenDetailScreen";

/** Hand-typed fixtures for the teams and API tokens screens. */

export const LONG =
  "platform-engineering-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

const DAY = 24 * 60 * 60 * 1000;
const inDays = (d: number) => new Date(Date.now() + d * DAY).toISOString();

const ORG = { id: "org-1", slug: "acme", name: "Acme" };

function team(id: string, slug: string, name: string, createdAt: string): AstroliftTeam {
  return {
    id,
    slug,
    name,
    createdAt,
    updatedAt: createdAt,
    deletedAt: null,
    organization: ORG,
  };
}

export const TEAMS: AstroliftTeam[] = [
  team("team-1", "platform", "Platform", "2026-06-02T10:00:00Z"),
  team("team-2", "payments", "Payments", "2026-07-14T09:30:00Z"),
  team("team-3", "data-science", "Data science", "2026-09-01T16:45:00Z"),
];

export const TEAM_LONG = team("team-9", LONG, LONG, "2026-09-20T08:00:00Z");

// ----------------------------------------------------------------- API tokens

const OWNER = { id: "u-1", username: "leo", email: "leo@example.com", isActive: true };

function token(id: string, opts: Partial<AstroliftApiToken> = {}): AstroliftApiToken {
  return {
    id,
    name: "ci-runner-prod",
    tokenLast4: "9f3a",
    scopes: ["read:apps", "read:clusters", "mcp:read"],
    effectivePermissions: ["app.read", "cluster.read", "agent.read"],
    createdAt: "2026-08-01T12:00:00Z",
    expiresAt: inDays(60),
    isRevoked: false,
    lastUsedAt: "2026-09-27T18:22:00Z",
    lastUsedIp: "203.0.113.24",
    lastUsedAgent: "astrolift-cli/3.3.1 (darwin; arm64)",
    teamSlug: "platform",
    user: OWNER,
    ...opts,
  };
}

export const TOKENS: AstroliftApiToken[] = [
  token("7c1e0f3a-0000-4000-8000-000000000001"),
  token("7c1e0f3a-0000-4000-8000-000000000002", {
    name: "ops-admin",
    tokenLast4: "b21c",
    scopes: ["admin"],
    effectivePermissions: ["app.read", "app.update", "cluster.manage", "team.update"],
    expiresAt: null,
    lastUsedIp: null,
    lastUsedAgent: null,
    teamSlug: null,
  }),
  token("7c1e0f3a-0000-4000-8000-000000000003", {
    name: "old-deploy-bot",
    tokenLast4: "00d4",
    scopes: [],
    effectivePermissions: [],
    isRevoked: true,
    lastUsedAt: null,
  }),
];

export const TOKEN_LONG = token("7c1e0f3a-0000-4000-8000-000000000009", {
  name: LONG,
  teamSlug: LONG,
  lastUsedAgent: `Mozilla/5.0 ${LONG} ${LONG}`,
  scopes: ["read:apps", "write:apps", "read:clusters", "write:clusters", "manage:clusters", LONG],
  effectivePermissions: [LONG, "app.read", "app.update", "cluster.read", "cluster.update"],
  user: { ...OWNER, username: LONG },
});

export const TOKEN_CREATED: AstroliftApiTokenPlaintext = {
  apiToken: TOKENS[0]!,
  plaintext: "alft_at_3kq9Zx7Lw2Vb8Nf4Rt6Yp1Hc5Jd0Ms9Ga7Ue2Io4Pl8",
};

export const TOKEN_DETAIL: TokenDetailScreenProps = {
  error: null,
  onRetry: async () => {},
  id: TOKENS[0]!.id,
  token: TOKENS[0]!,
  loading: false,
};

export const TOKEN_DETAIL_LONG: TokenDetailScreenProps = {
  error: null,
  onRetry: async () => {},
  id: TOKEN_LONG.id,
  token: TOKEN_LONG,
  loading: false,
};

// ---------------------------------------------------------------- ScopePicker

export const SCOPE_CATALOG: AstroliftApiTokenScopeCatalog = {
  scopes: [
    {
      value: "read:apps",
      label: "Read apps",
      surface: "apps",
      description: "List apps, deployments, environments and secret names.",
      sensitive: false,
      permissions: ["app.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "read:clusters",
      label: "Read clusters",
      surface: "clusters",
      description: "List clusters and read provider state.",
      sensitive: false,
      permissions: ["provider_plugin.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "write:clusters",
      label: "Update clusters",
      surface: "clusters",
      description: "Change a cluster's settings: ingress class, auth gate, region, endpoint.",
      sensitive: false,
      permissions: ["cluster.update"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "manage:clusters",
      label: "Operate clusters",
      surface: "clusters",
      description:
        "Run a cluster's recipe install, refresh its management state, reconcile its ingresses.",
      sensitive: false,
      permissions: ["cluster.manage", "cluster.bootstrap"],
      available: false,
      unavailableReason: "Your roles grant none of what this scope unlocks.",
    },
    {
      value: "mcp:read",
      label: "MCP read",
      surface: "agents",
      description: "List agent packages and inspect runs through remote MCP.",
      sensitive: false,
      permissions: ["agent.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "admin",
      label: "Admin",
      surface: "administration",
      description:
        "Everything the owner can do, now and as new permissions are added. Prefer the narrow scopes.",
      sensitive: true,
      permissions: ["cluster.update", "cluster.manage"],
      available: true,
      unavailableReason: "",
    },
  ],
  presets: [
    {
      key: "cluster_operator",
      label: "Cluster operator",
      scopes: ["read:clusters", "write:clusters", "manage:clusters"],
    },
    { key: "read_only", label: "Read-only", scopes: ["read:apps", "read:clusters", "mcp:read"] },
  ],
};

type ScopePickerData = Omit<ScopePickerProps, "value" | "onChange">;

export const SCOPE_PICKER: ScopePickerData = {
  catalog: SCOPE_CATALOG,
  loading: false,
  error: undefined,
};

export const SCOPE_CATALOG_LONG: AstroliftApiTokenScopeCatalog = {
  scopes: [
    {
      value: `read:${LONG}`,
      label: LONG,
      surface: LONG,
      description: `${LONG} ${LONG}`,
      sensitive: true,
      permissions: [LONG, `${LONG}.update`],
      available: false,
      unavailableReason: `${LONG} ${LONG}`,
    },
    ...SCOPE_CATALOG.scopes,
  ],
  presets: [{ key: "long", label: LONG, scopes: [`read:${LONG}`] }, ...SCOPE_CATALOG.presets],
};
