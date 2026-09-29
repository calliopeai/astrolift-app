"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { useWalk } from "@/components/screens/members/use-walk";
import { SOFT_DELETE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS, LIST_TEAMS_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { selectTeams, TEAMS_LIST } from "./teams-list";

/**
 * The Teams list: URL list state, the teams walk for the current search, and
 * the soft-delete mutation. The data half of TeamsScreen.
 */
export function useTeams() {
  const perms = useMyPermissions();
  const list = useListState(TEAMS_LIST);
  const { state } = list;
  const walk = useWalk<AstroliftTeam>(
    LIST_TEAMS_PAGE,
    (d) =>
      (d as { astroliftTeamsPage?: CursorPage<AstroliftTeam> } | undefined)?.astroliftTeamsPage,
    { variables: { search: state.q.trim() || null } }
  );
  const selected = selectTeams(walk.rows, {
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const [softDeleteTeam, { loading: deleting }] = useMutation<{
    softDeleteTeam: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_TEAM, {
    // LIST_TEAMS still backs the pickers on other surfaces; "ListTeamsPage"
    // is this list's own walk.
    refetchQueries: [{ query: LIST_TEAMS }, "ListTeamsPage"],
    awaitRefetchQueries: true,
  });

  /** Throws on failure so the confirm dialog stays open and shows the error. */
  async function onDelete(team: AstroliftTeam) {
    const { data } = await softDeleteTeam({ variables: { input: { id: team.id } } });
    const result = data?.softDeleteTeam;
    if (result?.ok) toast.success(`Deleted ${team.slug}`);
    else throw new Error(result?.errors?.[0]?.message ?? "Delete failed");
  }

  return {
    list,
    rows: selected.rows,
    totalCount: selected.totalCount,
    loading: walk.loading,
    stale: walk.stale,
    error: walk.error,
    truncated: walk.truncated,
    onRetry: walk.refetch,
    canUpdate: perms.can("team.update"),
    canDelete: perms.can("team.delete"),
    deleting,
    onDelete,
  };
}
