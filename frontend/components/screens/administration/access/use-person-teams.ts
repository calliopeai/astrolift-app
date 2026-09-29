"use client";

import * as React from "react";

import type { CursorPage, SortState } from "@/components/data-table";
import { type ListDefinition, useListState } from "@/components/list/use-list-state";
import { useWalk } from "@/components/screens/members/use-walk";
import { LIST_ROLE_BINDINGS_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftMember, AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { teamSlugIndex } from "./principal-access";

export interface TeamMembershipRow {
  /** The TEAM-scope Member row. */
  member: AstroliftMember;
  /** The team's pk, which is all a Member row carries. */
  pk: string;
  /** Named from a TEAM binding's label, where one exists. */
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
 * LIST_MEMBERS read), named from their own bindings' labels. A team they are
 * on without a binding there stays `team #<pk>` until the Member row carries
 * the team's name (design 6).
 */
export function usePersonTeams(
  member: AstroliftMember | null,
  memberships: readonly AstroliftMember[]
) {
  const list = useListState(PERSON_TEAMS_LIST);
  const bindings = useWalk<AstroliftRoleBinding>(
    LIST_ROLE_BINDINGS_PAGE,
    (d) =>
      (d as { astroliftRoleBindingsPage?: CursorPage<AstroliftRoleBinding> } | undefined)
        ?.astroliftRoleBindingsPage,
    { variables: { search: member?.user.username ?? null }, skip: !member }
  );

  const rows = React.useMemo<TeamMembershipRow[]>(() => {
    const names = teamSlugIndex(bindings.rows.filter((b) => b.user?.id === member?.user.id));
    return memberships
      .filter((m) => m.scopeKind === "TEAM")
      .map((m) => ({ member: m, pk: m.scopeId, slug: names.get(m.scopeId) ?? null }));
  }, [bindings.rows, member, memberships]);

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
    stale: bindings.stale,
    error: bindings.error,
    onRetry: bindings.refetch,
  };
}
