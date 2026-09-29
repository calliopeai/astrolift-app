"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

/**
 * One team, found in the org's team list (there is no singular team query).
 * Shared by every tab of the team's page from the cache; each tab's own data
 * comes from its own hook.
 */
export function useTeamDetail(slug: string) {
  const perms = useMyPermissions();
  const teams = useQuery<TeamsResp>(LIST_TEAMS);
  const team = (teams.data?.astroliftTeams ?? []).find((t) => t.slug === slug) ?? null;
  return {
    slug,
    team,
    canManage: perms.can("org.manage_members"),
    loading: teams.loading && !team,
    error: teams.error && !teams.data ? { message: teams.error.message } : null,
    onRetry: () => {
      void teams.refetch();
    },
  };
}
