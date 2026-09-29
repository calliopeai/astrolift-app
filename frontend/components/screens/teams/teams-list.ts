/**
 * Admin › Access › Teams (access UX design 3.1) and a team's Members tab:
 * the list declarations and the pure filter, sort and page step.
 *
 * `astroliftTeamsPage` takes `search`, `limit` and `after` only, so the hook
 * walks the org's teams (a few hundred at most) and numbers the pages here,
 * as the Clusters list does. Mine cannot be answered: a TEAM-scope Member
 * row carries the team's integer pk and a team is exposed by GUID only, so
 * nothing joins "teams I am on" to this list yet (design 6).
 *
 * `astroliftTeamMembers` returns up to 500 rows unpaged, so a team's members
 * are filtered, sorted and paged here too.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftMember, AstroliftTeam } from "@/graphql/identity/identity.types";

export const TEAMS_LIST: ListDefinition = {
  id: "admin.access.teams",
  fields: [],
  // The server matches team name, slug and description.
  searchPlaceholder: "Search teams…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ mine: "me" }, [], {
    mineNote:
      "The teams query does not say who is on a team yet, so Mine is empty. A person's Teams tab lists the teams they are on.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

function ordered<T>(
  rows: T[],
  sort: SortState[],
  value: Record<string, (r: T) => string | number>,
  tie: (r: T) => string
) {
  return [...rows].sort((a, b) => {
    for (const s of sort) {
      const v = value[s.key];
      if (!v) continue;
      const x = v(a);
      const y = v(b);
      if (x < y) return s.dir === "asc" ? -1 : 1;
      if (x > y) return s.dir === "asc" ? 1 : -1;
    }
    return tie(a) < tie(b) ? -1 : 1;
  });
}

function page<T>(rows: T[], p: number, size: number) {
  const start = (Math.max(1, p) - 1) * size;
  return rows.slice(start, start + size);
}

const TEAM_SORT: Record<string, (t: AstroliftTeam) => string | number> = {
  name: (t) => t.name.toLowerCase(),
  created: (t) => Date.parse(t.createdAt) || 0,
};

export function selectTeams(
  teams: readonly AstroliftTeam[],
  {
    filters,
    sort,
    page: p,
    pageSize,
  }: { filters: Record<string, string>; sort: SortState[]; page: number; pageSize: number }
): { rows: AstroliftTeam[]; totalCount: number } {
  const kept = filters.mine ? [] : ordered([...teams], sort, TEAM_SORT, (t) => t.slug);
  return { rows: page(kept, p, pageSize), totalCount: kept.length };
}

// ---------------------------------------------------------------------------
// A team's members
// ---------------------------------------------------------------------------

export const TEAM_MEMBERS_LIST: ListDefinition = {
  id: "admin.access.team.members",
  fields: [
    {
      key: "status",
      label: "Status",
      options: ["active", "invited", "suspended"].map((v) => ({ value: v, label: v })),
    },
  ],
  searchPlaceholder: "Search members…",
  defaultSort: [{ key: "user", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const MEMBER_SORT: Record<string, (m: AstroliftMember) => string | number> = {
  user: (m) => m.user.username.toLowerCase(),
  joined: (m) => Date.parse(m.joinedAt ?? m.createdAt) || 0,
};

export function selectTeamMembers(
  members: readonly AstroliftMember[],
  {
    filters,
    q,
    sort,
    page: p,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): { rows: AstroliftMember[]; totalCount: number } {
  const needle = q.trim().toLowerCase();
  const kept = ordered(
    members.filter(
      (m) =>
        (!filters.status || m.lifecycle === filters.status) &&
        (!needle || `${m.user.username} ${m.user.email}`.toLowerCase().includes(needle))
    ),
    sort,
    MEMBER_SORT,
    (m) => m.id
  );
  return { rows: page(kept, p, pageSize), totalCount: kept.length };
}
