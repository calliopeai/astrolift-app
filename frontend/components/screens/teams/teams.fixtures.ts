/**
 * Story fixtures for the Teams list and a team's page, typed against each
 * screen's props and selected the way the hooks select them.
 */
import type { ListStateController } from "@/components/list/list-state";
import type {
  AstroliftMember,
  AstroliftProject,
  AstroliftRole,
  AstroliftTeam,
} from "@/graphql/identity/identity.types";

import { LONG, TEAM_LONG, TEAMS } from "./teams-tokens.fixtures";
import { selectTeamMembers } from "./teams-list";
import type { TeamDetailScreenProps } from "./TeamDetailScreen";
import type { TeamMembersPanelProps } from "./TeamMembersPanel";
import type { TeamsScreenProps } from "./TeamsScreen";

export { LONG, TEAM_LONG, TEAMS };

const noop = () => {};

type TeamsData = Omit<TeamsScreenProps, "list" | "renderCreateDialog" | "renderEditDialog">;

/** The page the server answers: `teams` as the rows, `totalCount` their count unless given. */
export function teamsScreenProps(
  teams: AstroliftTeam[] = TEAMS,
  overrides: Partial<TeamsData> = {}
): TeamsData {
  return {
    rows: teams,
    totalCount: teams.length,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    canUpdate: true,
    canDelete: true,
    deleting: false,
    onDelete: async () => {},
    ...overrides,
  };
}

export const ROLES: AstroliftRole[] = [
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
    scopeId: "14",
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

export const MEMBERS_LONG: AstroliftMember[] = [
  member("m-9", LONG, `${LONG}@example.com`, { lifecycle: "invited" }),
  ...MEMBERS,
];

type MembersData = Omit<TeamMembersPanelProps, "list">;

export function membersPanelProps(
  list: ListStateController,
  members: AstroliftMember[] = MEMBERS,
  overrides: Partial<MembersData> = {}
): MembersData {
  const selected = selectTeamMembers(members, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return {
    team: TEAMS[0]!,
    rows: selected.rows,
    totalCount: selected.totalCount,
    loading: false,
    error: null,
    onRetry: noop,
    roles: ROLES,
    canManageTeamMembers: true,
    assigning: false,
    onAssign: async () => true,
    ...overrides,
  };
}

export const TEAM_DETAIL: Omit<TeamDetailScreenProps, "tab" | "children"> = {
  slug: "platform",
  team: TEAMS[0]!,
  canManage: true,
  loading: false,
  error: null,
  onRetry: noop,
};

export const TEAM_DETAIL_LONG: typeof TEAM_DETAIL = {
  ...TEAM_DETAIL,
  slug: LONG,
  team: TEAM_LONG,
};

const ORG = { id: "org-1", slug: "acme", name: "Acme" };
const project = (id: string, slug: string, name: string): AstroliftProject => ({
  id,
  slug,
  name,
  organization: ORG,
  team: { id: "team-1", slug: "platform", name: "Platform" },
  createdAt: "2026-06-02T10:00:00Z",
  updatedAt: "2026-06-02T10:00:00Z",
  deletedAt: null,
});

export const PROJECTS: AstroliftProject[] = [
  project("p-1", "checkout", "Checkout"),
  project("p-2", "ledger", "Ledger"),
  project("p-3", `reporting-${LONG}`, `Reporting ${LONG}`),
];
