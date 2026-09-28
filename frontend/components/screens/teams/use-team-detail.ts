"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_ROLES, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftRole, AstroliftTeam } from "@/graphql/identity/identity.types";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

/** One team, found in the org's team list, plus the roles a team can grant. */
export function useTeamDetail(slug: string) {
  const teams = useQuery<TeamsResp>(LIST_TEAMS);
  const roles = useQuery<RolesResp>(LIST_ROLES);

  const team = (teams.data?.astroliftTeams ?? []).find((t) => t.slug === slug) ?? null;

  // Filter roles down to team-grantable scope levels so the assign
  // dialog can't offer a PROJECT/APP-scoped role that the backend
  // would reject anyway.
  const grantableRoles = (roles.data?.astroliftRoles ?? []).filter(
    (r) => r.scopeLevel === "TEAM" || r.scopeLevel === "ORG"
  );

  return { slug, team, loading: teams.loading && !team, grantableRoles };
}
