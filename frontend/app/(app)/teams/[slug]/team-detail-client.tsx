"use client";

import { TeamDetailScreen } from "@/components/screens/teams/TeamDetailScreen";
import { useTeamDetail } from "@/components/screens/teams/use-team-detail";

import { TeamMembersPanel } from "./team-members-panel";

export function TeamDetailClient({ slug }: { slug: string }) {
  return (
    <TeamDetailScreen
      {...useTeamDetail(slug)}
      renderMembers={(team, roles) => <TeamMembersPanel team={team} roles={roles} />}
    />
  );
}
