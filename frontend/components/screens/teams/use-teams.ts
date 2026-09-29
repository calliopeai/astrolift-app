"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { useNumberedListQuery } from "@/components/screens/administration/access/use-list-page-query";
import { SOFT_DELETE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS, LIST_TEAMS_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { TEAMS_LIST, teamsFilter } from "./teams-list";

/**
 * The Teams list: URL list state, one numbered page of `astroliftTeamsPage`
 * for it (search, Mine, sort and page on the server), and the soft-delete
 * mutation. The data half of TeamsScreen.
 */
export function useTeams() {
  const perms = useMyPermissions();
  const list = useListState(TEAMS_LIST);
  const page = useNumberedListQuery<AstroliftTeam, ReturnType<typeof teamsFilter>>(
    LIST_TEAMS_PAGE,
    list,
    (d) =>
      (d as { astroliftTeamsPage?: CursorPage<AstroliftTeam> } | undefined)?.astroliftTeamsPage,
    { toFilter: teamsFilter }
  );

  const [softDeleteTeam, { loading: deleting }] = useMutation<{
    softDeleteTeam: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_TEAM, {
    // LIST_TEAMS still backs the pickers on other surfaces; "ListTeamsPage"
    // is this list's own page.
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
    rows: page.rows,
    totalCount: page.totalCount ?? page.rows.length,
    loading: page.loading,
    stale: page.stale,
    error: page.error,
    onRetry: page.refetch,
    canUpdate: perms.can("team.update"),
    canDelete: perms.can("team.delete"),
    deleting,
    onDelete,
  };
}
