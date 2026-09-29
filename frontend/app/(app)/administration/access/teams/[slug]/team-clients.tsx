"use client";

import { holderLabel } from "@/components/screens/administration/access/principal-access";
import { usePrincipalAccess } from "@/components/screens/administration/access/use-principal-access";
import { TeamAccessPanel } from "@/components/screens/teams/TeamAccessPanel";
import { TeamDetailScreen } from "@/components/screens/teams/TeamDetailScreen";
import { TeamMembersPanel } from "@/components/screens/teams/TeamMembersPanel";
import { useTeamDetail } from "@/components/screens/teams/use-team-detail";
import { useTeamMembers } from "@/components/screens/teams/use-team-members";
import { useTeamProjects } from "@/components/screens/teams/use-team-projects";

export function TeamAccessClient({ slug }: { slug: string }) {
  const detail = useTeamDetail(slug);
  const access = usePrincipalAccess(detail.team ? { kind: "team", slug } : null);
  const reach = useTeamProjects(slug);
  return (
    <TeamDetailScreen {...detail} tab="access">
      <TeamAccessPanel slug={slug} access={{ ...access, holderLabel }} reach={reach} />
    </TeamDetailScreen>
  );
}

export function TeamMembersClient({ slug }: { slug: string }) {
  const detail = useTeamDetail(slug);
  const members = useTeamMembers(detail.team);
  return (
    <TeamDetailScreen {...detail} tab="members">
      {detail.team && <TeamMembersPanel {...members} team={detail.team} />}
    </TeamDetailScreen>
  );
}
