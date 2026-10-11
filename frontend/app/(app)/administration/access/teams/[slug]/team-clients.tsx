"use client";

import { useTranslations } from "next-intl";
import { grantHref, TEAMS_HREF } from "@/components/screens/administration/access/access-nav";
import { useEntityAccess } from "@/components/screens/administration/access/use-entity-access";
import { TeamAccessPanel } from "@/components/screens/teams/TeamAccessPanel";
import { TeamDetailScreen } from "@/components/screens/teams/TeamDetailScreen";
import { MembershipRouteClient } from "@/components/screens/administration/access/team-memberships/MembershipRouteClient";
import { useTeamDetail } from "@/components/screens/teams/use-team-detail";
import { useTeamProjects } from "@/components/screens/teams/use-team-projects";

export function TeamAccessClient({ slug }: { slug: string }) {
  const t = useTranslations("teams.access");
  const detail = useTeamDetail(slug);
  const team = detail.team;
  const access = useEntityAccess(
    team ? { kind: "TEAM", id: team.id } : null,
    t("subject", { name: slug })
  );
  const reach = useTeamProjects(slug);
  const grant = team
    ? grantHref({
        scope: { kind: "TEAM", id: team.id, name: team.slug },
        returnTo: `${TEAMS_HREF}/${encodeURIComponent(slug)}`,
      })
    : undefined;
  return (
    <TeamDetailScreen {...detail} tab="access">
      <TeamAccessPanel slug={slug} access={{ ...access, grantHref: grant }} reach={reach} />
    </TeamDetailScreen>
  );
}

export function TeamMembersClient({ slug }: { slug: string }) {
  return <MembershipRouteClient direction="team" slug={slug} />;
}
