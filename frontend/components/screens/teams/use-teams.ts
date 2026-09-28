"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { SOFT_DELETE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS, LIST_TEAMS_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";

interface TeamsPageResp {
  astroliftTeamsPage: CursorPage<AstroliftTeam>;
}

/**
 * The org teams table: the paged walk and the soft-delete mutation. The data
 * half of TeamsScreen.
 */
export function useTeams() {
  // `astroliftTeamsPage` takes `search`, `limit` and `after` only — there is
  // no sort argument, so no column declares a `sortKey` and the header stays
  // a plain label rather than a control that could only reorder one page.
  const table = useCursorTable<AstroliftTeam>({
    query: LIST_TEAMS_PAGE,
    extract: (d) => (d as TeamsPageResp | undefined)?.astroliftTeamsPage,
    searchVariable: "search",
    urlKey: "team",
  });

  const [softDeleteTeam, { loading: deleting }] = useMutation<{
    softDeleteTeam: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_TEAM, {
    // LIST_TEAMS still backs the pickers on other surfaces; "ListTeamsPage"
    // is this table's own walk, which is a different root field and would
    // otherwise keep showing the deleted row.
    refetchQueries: [{ query: LIST_TEAMS }, "ListTeamsPage"],
    awaitRefetchQueries: true,
  });

  /** Throws on failure so the confirm dialog stays open and shows the error. */
  async function onDelete(team: AstroliftTeam) {
    const { data } = await softDeleteTeam({
      variables: { input: { id: team.id } },
    });
    const result = data?.softDeleteTeam;
    if (result?.ok) {
      toast.success(`Deleted ${team.slug}`);
    } else {
      throw new Error(result?.errors?.[0]?.message ?? "Delete failed");
    }
  }

  return { table, deleting, onDelete };
}
