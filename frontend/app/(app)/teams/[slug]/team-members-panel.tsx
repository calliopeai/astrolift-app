"use client";

import { TeamMembersPanel as TeamMembersPanelView } from "@/components/screens/teams/TeamMembersPanel";
import { useTeamMembers } from "@/components/screens/teams/use-team-members";
import type { AstroliftRole, AstroliftTeam } from "@/graphql/identity/identity.types";

interface Props {
  team: Pick<AstroliftTeam, "id" | "slug" | "name">;
  roles: AstroliftRole[];
}

/** Bulk role-assign panel for a single team (#416 scope B). */
export function TeamMembersPanel({ team, roles }: Props) {
  return <TeamMembersPanelView {...useTeamMembers(team)} team={team} roles={roles} />;
}
