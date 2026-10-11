"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import type {
  GetMembershipTeamQuery,
  GetMembershipTeamQueryVariables,
} from "@/graphql/__generated__/operations";
import { GET_MEMBERSHIP_TEAM } from "@/graphql/identity/team-memberships.queries";
import { useTeamMembers } from "@/components/screens/teams/use-team-members";
import { TeamMembersPanel } from "@/components/screens/teams/TeamMembersPanel";
import { TEAMS_HREF } from "../access-nav";
import { useTeamMembershipScope } from "./use-team-membership-scope";
import { useTeamAccessNavigation } from "./use-team-access-navigation";

export function LegacyTeamAssignmentsClient({ slug }: { slug: string }) {
  const scope = useTeamMembershipScope();
  return <LegacyAssignmentsContext key={scope.key + slug} slug={slug} ready={scope.ready} />;
}

function LegacyAssignmentsContext({ slug, ready }: { slug: string; ready: boolean }) {
  const t = useTranslations("teams.memberships");
  const navigation = useTeamAccessNavigation(ready);
  const query = useQuery<GetMembershipTeamQuery, GetMembershipTeamQueryVariables>(
    GET_MEMBERSHIP_TEAM,
    {
      variables: { slug },
      skip: !ready,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const team =
    ready &&
    !query.loading &&
    !query.error &&
    navigation.navigation?.canViewPeople &&
    query.data?.astroliftTeamMembershipTeam?.canManageMembers
      ? query.data.astroliftTeamMembershipTeam
      : null;
  const members = useTeamMembers(team, { fresh: true });
  return (
    <LegacyAssignmentsPanel
      slug={slug}
      loading={query.loading || navigation.loading}
      error={query.error?.message ?? navigation.error?.message ?? (!team ? t("unavailable") : null)}
    >
      {team && <TeamMembersPanel key={team.id} team={team} {...members} memberHref={null} />}
    </LegacyAssignmentsPanel>
  );
}

export function LegacyAssignmentsPanel({
  slug,
  loading,
  error,
  children,
}: {
  slug: string;
  loading: boolean;
  error: string | null;
  children: ReactNode;
}) {
  const t = useTranslations("teams.memberships");
  return (
    <div className="flex min-w-0 flex-col gap-4 p-6">
      <Link className="underline" href={`${TEAMS_HREF}/${encodeURIComponent(slug)}/members`}>
        {t("people")}
      </Link>
      <h1 className="text-xl font-semibold">{t("bulkAssign")}</h1>
      <p>{t("legacyBulkLimit")}</p>
      {loading ? <p>{t("loading")}</p> : error ? <p role="alert">{error}</p> : children}
    </div>
  );
}
