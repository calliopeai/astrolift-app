"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { type ListDefinition, useLocalListState } from "@/components/list/use-list-state";
import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import {
  GRANT_TEAM_ACCESS_TO_APP,
  MOVE_APP_TO_TEAM,
  REVOKE_TEAM_ACCESS_FROM_APP,
} from "@/graphql/registry/registry.mutations";
import {
  GET_APP,
  LIST_APP_TEAM_ACCESSES,
  LIST_APP_TEAM_ACCESSES_PAGE,
} from "@/graphql/registry/registry.queries";
import type { AppTeamAccessLevel, AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

import { useCursorList } from "../use-cursor-list";

interface TeamAccessesPageResp {
  astroliftAppTeamAccessesPage: CursorPage<AstroliftAppTeamAccess>;
}
interface TeamAccessesResp {
  astroliftAppTeamAccesses: AstroliftAppTeamAccess[];
}
interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

/**
 * The grants as a list (spec 44 §5.1): cursor pages of
 * `astroliftAppTeamAccessesPage`, which searches the team's name and slug
 * and takes no filter and no sort, so the list declares none. Its state is
 * in memory: the card sits in a settings section, whose `?section=` a URL
 * list state would drop.
 */
export const APP_TEAM_ACCESS_LIST: ListDefinition = {
  id: "apps.settings.team-access",
  fields: [],
  searchPlaceholder: "Search teams by name or slug…",
  defaultSort: [],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export interface UseTeamsCardArgs {
  appSlug: string;
  appId: string;
  /** Current home-team slug, surfaced as breadcrumb context. */
  homeTeamSlug: string;
}

/**
 * Data half of TeamsCardView: the paged grants list, the flat grant list
 * the Add-Team picker subtracts from, the org's teams, and the grant /
 * revoke / move mutations with their toasts.
 */
export function useTeamsCard({ appSlug, appId, homeTeamSlug }: UseTeamsCardArgs) {
  // The list's rows: one page of `astroliftAppTeamAccessesPage`.
  const grants = useCursorList<AstroliftAppTeamAccess>({
    query: LIST_APP_TEAM_ACCESSES_PAGE,
    variables: { appSlug },
    extract: (d) => (d as TeamAccessesPageResp | undefined)?.astroliftAppTeamAccessesPage,
    list: useLocalListState(APP_TEAM_ACCESS_LIST),
  });

  // The flat list stays, and it is not the table's source. It is what the
  // Add-Team picker subtracts from the org's teams to find candidates, so
  // deriving it from whichever page is on screen would offer teams that
  // already hold a grant — and granting one that exists is not a no-op,
  // it silently changes that team's access level.
  const accesses = useQuery<TeamAccessesResp>(LIST_APP_TEAM_ACCESSES, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const teams = useQuery<TeamsResp>(LIST_TEAMS, { fetchPolicy: "cache-first" });

  const refetch = [
    "ListAppTeamAccessesPage",
    { query: LIST_APP_TEAM_ACCESSES, variables: { appSlug } },
    { query: GET_APP, variables: { slug: appSlug } },
  ];

  const [grant, { loading: granting }] = useMutation<{
    grantTeamAccessToApp: MutationResult<AstroliftAppTeamAccess>;
  }>(GRANT_TEAM_ACCESS_TO_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const [revoke, { loading: revoking }] = useMutation<{
    revokeTeamAccessFromApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_TEAM_ACCESS_FROM_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const [move, { loading: moving }] = useMutation<{
    moveAppToTeam: MutationResult<{ id: string; slug: string; teamSlug: string }>;
  }>(MOVE_APP_TO_TEAM, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  // The Add-Team sheet's grant: its own loading state and refetch list,
  // as the sheet had before it moved out of the route.
  const [addGrant, { loading: adding }] = useMutation<{
    grantTeamAccessToApp: MutationResult<AstroliftAppTeamAccess>;
  }>(GRANT_TEAM_ACCESS_TO_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const list = accesses.data?.astroliftAppTeamAccesses ?? [];
  const teamList = teams.data?.astroliftTeams ?? [];

  // Teams in this org that don't yet hold an active access grant —
  // candidates for the Add-Team picker. We don't pre-filter by org
  // here because LIST_TEAMS already returns the active-tenant's teams.
  const grantedTeamIds = new Set(list.map((a) => a.teamId));
  const candidateTeams = teamList.filter((t) => !grantedTeamIds.has(t.id));

  async function onLevelChange(access: AstroliftAppTeamAccess, level: AppTeamAccessLevel) {
    if (access.accessLevel === level) return;
    const { data } = await grant({
      variables: {
        input: { appId, teamId: access.teamId, accessLevel: level },
      },
    });
    const result = data?.grantTeamAccessToApp;
    if (result?.ok) {
      toast.success(`Updated ${access.teamSlug} to ${level}.`);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Update failed.");
    }
  }

  async function onRevoke(access: AstroliftAppTeamAccess) {
    const { data } = await revoke({
      variables: { input: { appId, teamId: access.teamId } },
    });
    const result = data?.revokeTeamAccessFromApp;
    if (result?.ok) {
      toast.success(`Revoked ${access.teamSlug}'s access.`);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Revoke failed.");
    }
  }

  /** Resolves true when the move landed, so the view can close its sheet. */
  async function onMove(targetTeamId: string): Promise<boolean> {
    const { data } = await move({
      variables: { input: { appId, targetTeamId } },
    });
    const result = data?.moveAppToTeam;
    if (result?.ok) {
      toast.success("App moved to the new home team.");
      return true;
    }
    toast.error(result?.errors?.[0]?.message ?? "Move failed.");
    return false;
  }

  /** Resolves true when the grant landed, so the view can close its sheet. */
  async function onAddTeam(teamId: string, level: AppTeamAccessLevel): Promise<boolean> {
    const { data } = await addGrant({
      variables: { input: { appId, teamId, accessLevel: level } },
    });
    const result = data?.grantTeamAccessToApp;
    if (result?.ok) {
      toast.success(`Granted access at level ${level}.`);
      return true;
    }
    toast.error(result?.errors?.[0]?.message ?? "Grant failed.");
    return false;
  }

  return {
    homeTeamSlug,
    ...grants,
    teams: teamList,
    teamsLoading: teams.loading,
    candidateTeams,
    granting,
    revoking,
    moving,
    adding,
    onLevelChange,
    onRevoke,
    onMove,
    onAddTeam,
  };
}
