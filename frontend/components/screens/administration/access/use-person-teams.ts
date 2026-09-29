"use client";

import * as React from "react";

import type { SortState } from "@/components/data-table";
import { type ListDefinition, useListState } from "@/components/list/use-list-state";
import type { AstroliftMember } from "@/graphql/identity/identity.types";

export interface TeamMembershipRow {
  /** The TEAM-scope Member row. */
  member: AstroliftMember;
  /** The team's pk, the row's `scopeId`. */
  pk: string;
  /** The team's slug, which the server sets on a TEAM-scope row. */
  slug: string | null;
}

export const PERSON_TEAMS_LIST: ListDefinition = {
  id: "admin.access.person.teams",
  fields: [],
  searchPlaceholder: "Search teams…",
  defaultSort: [{ key: "team", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const SORT_VALUE: Record<string, (r: TeamMembershipRow) => string | number> = {
  team: (r) => r.slug ?? `~${r.pk}`,
  joined: (r) => Date.parse(r.member.joinedAt ?? r.member.createdAt) || 0,
};

/** Filter by search, sort, number the pages. Pure. */
export function selectMemberships(
  rows: readonly TeamMembershipRow[],
  { q, sort, page, pageSize }: { q: string; sort: SortState[]; page: number; pageSize: number }
): { rows: TeamMembershipRow[]; totalCount: number } {
  const needle = q.trim().toLowerCase();
  const kept = rows
    .filter((r) => !needle || (r.slug ?? r.pk).toLowerCase().includes(needle))
    .sort((a, b) => {
      for (const s of sort) {
        const v = SORT_VALUE[s.key];
        if (!v) continue;
        const x = v(a);
        const y = v(b);
        if (x !== y) return (x < y ? -1 : 1) * (s.dir === "asc" ? 1 : -1);
      }
      return a.pk < b.pk ? -1 : 1;
    });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
}

/**
 * A person's Teams tab: their TEAM-scope Member rows (from the page's
 * LIST_MEMBERS read), each named by the team the server puts on the row
 * (`teamSlug`). A person is on a handful of teams, so the search, sort and
 * page run here over them.
 */
export function usePersonTeams(
  member: AstroliftMember | null,
  memberships: readonly AstroliftMember[]
) {
  const list = useListState(PERSON_TEAMS_LIST);

  const rows = React.useMemo<TeamMembershipRow[]>(
    () =>
      memberships
        .filter((m) => m.scopeKind === "TEAM")
        .map((m) => ({ member: m, pk: m.scopeId, slug: m.teamSlug ?? null })),
    [memberships]
  );

  const selected = selectMemberships(rows, {
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });

  return {
    list,
    rows: selected.rows,
    totalCount: selected.totalCount,
    loading: !member,
    error: null,
    onRetry: () => {},
  };
}
