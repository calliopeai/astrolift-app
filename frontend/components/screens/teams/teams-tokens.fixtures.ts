import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftApiTokenScopeCatalog } from "@/graphql/__generated__/schema";
import type {
  AstroliftApiToken,
  AstroliftApiTokenPlaintext,
  AstroliftMember,
  AstroliftRole,
  AstroliftTeam,
} from "@/graphql/identity/identity.types";

import type { ScopePickerProps } from "../tokens/ScopePicker";
import type { TokenDetailScreenProps } from "../tokens/TokenDetailScreen";
import type { TokensScreenProps } from "../tokens/TokensScreen";

import type { TeamDetailScreenProps } from "./TeamDetailScreen";
import type { TeamMembersPanelProps } from "./TeamMembersPanel";
import type { TeamsScreenProps } from "./TeamsScreen";

/** Hand-typed fixtures for the teams and API tokens screens. */

const noop = () => {};
const asyncNoop = async () => {};
const yes = async () => true;

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

// ---------------------------------------------------------------- TeamsScreen

type TeamsData = Omit<TeamsScreenProps, "renderCreateDialog" | "renderEditDialog">;

export const TEAMS_SCREEN: TeamsData = {
  table: fakeController<AstroliftTeam>({ rows: TEAMS, totalCount: TEAMS.length }),
  deleting: false,
  onDelete: asyncNoop,
};

export const TEAMS_SCREEN_LONG: TeamsData = {
  ...TEAMS_SCREEN,
  table: fakeController<AstroliftTeam>({ rows: [TEAM_LONG, ...TEAMS], totalCount: 4 }),
};

// ----------------------------------------------------------- TeamMembersPanel

const ROLES: AstroliftRole[] = [
  {
    id: "role-1",
    slug: "team-admin",
    name: "Team admin",
    description: "Manage the team and its projects.",
    isSystem: true,
    permissions: ["team.update", "team.manage_members"],
    scopeLevel: "TEAM",
  },
  {
    id: "role-2",
    slug: "org-viewer",
    name: "Org viewer",
    description: "Read everything in the org.",
    isSystem: true,
    permissions: ["org.read"],
    scopeLevel: "ORG",
  },
];

function member(
  id: string,
  username: string,
  email: string,
  opts: Partial<AstroliftMember> = {}
): AstroliftMember {
  return {
    id,
    isActive: true,
    lifecycle: "active",
    createdAt: "2026-06-02T10:00:00Z",
    joinedAt: "2026-06-03T11:00:00Z",
    deletedAt: null,
    lastActiveAt: null,
    lastSeenAt: null,
    scopeId: "team-1",
    scopeKind: "TEAM",
    user: { id: `u-${id}`, username, email, isActive: true },
    ...opts,
  };
}

export const MEMBERS: AstroliftMember[] = [
  member("m-1", "leo", "leo@example.com"),
  member("m-2", "keith", "keith@example.com", { joinedAt: null }),
  member("m-3", "eric", "eric@example.com", { isActive: false, lifecycle: "suspended" }),
];

type MembersData = Omit<TeamMembersPanelProps, "team" | "roles">;

export const MEMBERS_PANEL: MembersData & Pick<TeamMembersPanelProps, "team" | "roles"> = {
  team: TEAMS[0]!,
  roles: ROLES,
  members: MEMBERS,
  loading: false,
  error: undefined,
  canManageTeamMembers: true,
  assigning: false,
  onAssign: yes,
};

export const MEMBERS_PANEL_LONG: typeof MEMBERS_PANEL = {
  ...MEMBERS_PANEL,
  team: TEAM_LONG,
  members: [member("m-9", LONG, `${LONG}@example.com`, { lifecycle: LONG }), ...MEMBERS],
};

// ----------------------------------------------------------- TeamDetailScreen

type TeamDetailData = Omit<TeamDetailScreenProps, "renderMembers">;

export const TEAM_DETAIL: TeamDetailData = {
  slug: "platform",
  team: TEAMS[0]!,
  loading: false,
  grantableRoles: ROLES,
};

export const TEAM_DETAIL_LONG: TeamDetailData = {
  slug: LONG,
  team: TEAM_LONG,
  loading: false,
  grantableRoles: ROLES,
};

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

type TokensData = Omit<TokensScreenProps, "renderScopePicker">;

export const TOKENS_SCREEN: TokensData = {
  table: fakeController<AstroliftApiToken>({ rows: TOKENS, totalCount: TOKENS.length }),
  creating: false,
  revoking: false,
  createdToken: null,
  onDismissCreated: noop,
  mcpEndpoint: "https://astrolift.example.com/api/mcp/v1/",
  onCreate: yes,
  onRevoke: asyncNoop,
  onCopyPlaintext: noop,
  onCopyMcpEndpoint: noop,
};

export const TOKENS_SCREEN_LONG: TokensData = {
  ...TOKENS_SCREEN,
  table: fakeController<AstroliftApiToken>({ rows: [TOKEN_LONG, ...TOKENS], totalCount: 4 }),
  createdToken: { apiToken: TOKEN_LONG, plaintext: `alft_at_${LONG}${LONG}` },
  mcpEndpoint: `https://${LONG}.example.com/api/mcp/v1/`,
};

export const TOKEN_DETAIL: TokenDetailScreenProps = {
  id: TOKENS[0]!.id,
  token: TOKENS[0]!,
  loading: false,
};

export const TOKEN_DETAIL_LONG: TokenDetailScreenProps = {
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
      description: "List apps and read their settings.",
      sensitive: false,
      permissions: ["app.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "read:clusters",
      label: "Read clusters",
      surface: "clusters",
      description: "List clusters.",
      sensitive: false,
      permissions: ["provider_plugin.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "write:clusters",
      label: "Update clusters",
      surface: "clusters",
      description: "Change a cluster's settings.",
      sensitive: false,
      permissions: ["cluster.update"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "manage:clusters",
      label: "Operate clusters",
      surface: "clusters",
      description: "Run a cluster's recipe.",
      sensitive: false,
      permissions: ["cluster.manage", "cluster.bootstrap"],
      available: false,
      unavailableReason: "Your roles grant none of what this scope unlocks.",
    },
    {
      value: "mcp:read",
      label: "MCP read",
      surface: "agents",
      description: "Inspect packages and tasks over MCP.",
      sensitive: false,
      permissions: ["agent.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "admin",
      label: "Admin",
      surface: "administration",
      description: "Everything its owner can do.",
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
    { key: "read_only", label: "Read only", scopes: ["read:apps", "read:clusters", "mcp:read"] },
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
