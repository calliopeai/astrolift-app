/**
 * Admin › Access › Teams (access UX design 3.1) and a team's Members tab:
 * the list declarations, and the team members' pure filter, sort and page.
 *
 * The Teams list is on `astroliftTeamsPage`'s list contract (#2153): Mine
 * (the teams the viewer is on), the name and created sorts and numbered
 * pages, all on the server.
 *
 * `astroliftTeamMembers` returns up to 500 rows unpaged, so a team's
 * members are filtered, sorted and paged here.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { AstroliftMember } from "@/graphql/identity/identity.types";

export const TEAMS_LIST: ListDefinition = {
  id: "admin.access.teams",
  fields: [],
  // The server matches team name, slug and description.
  searchPlaceholder: "Search teams…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ mine: "me" }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function localizedTeamsList(t: (key: string) => string): ListDefinition {
  return {
    ...TEAMS_LIST,
    searchPlaceholder: t("list.search"),
    views: TEAMS_LIST.views.map((view) => ({
      ...view,
      label: view.key === "all" || view.key === "mine" ? t(`list.${view.key}`) : view.label,
    })),
  };
}

export interface TeamsListFilter {
  mine?: boolean;
}

/** Mine is the viewer's own teams; the server knows who is on each. */
export function teamsFilter(filters: Record<string, string>): TeamsListFilter {
  return { mine: filters.mine === "me" ? true : undefined };
}

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

export function localizedTeamMembersList(t: (key: string) => string): ListDefinition {
  return {
    ...TEAM_MEMBERS_LIST,
    searchPlaceholder: t("search"),
    views: TEAM_MEMBERS_LIST.views.map((view) => ({
      ...view,
      label: view.key === "all" ? t("all") : view.label,
    })),
    fields: TEAM_MEMBERS_LIST.fields.map((field) => ({
      ...field,
      label: field.key === "status" ? t("status") : field.label,
      options: field.options?.map((option) => ({
        ...option,
        label: ["active", "invited", "suspended"].includes(option.value)
          ? t(option.value)
          : option.label,
      })),
    })),
  };
}

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
