"use client";

import { UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftRole, AstroliftTeam } from "@/graphql/identity/identity.types";

import type { useTeamDetail } from "./use-team-detail";

export type TeamDetailScreenProps = ReturnType<typeof useTeamDetail> & {
  /** The members table; it runs its own query, so the route supplies it. */
  renderMembers: (team: AstroliftTeam, roles: AstroliftRole[]) => React.ReactNode;
};

/** A team's page: its members, with bulk role assignment. */
export function TeamDetailScreen({
  slug,
  team,
  loading,
  grantableRoles,
  renderMembers,
}: TeamDetailScreenProps) {
  const tPage = useTranslations("lists.teamMembersBulk");

  if (loading) {
    return (
      <PageShell title={tPage("tabTitle")} description={tPage("description", { team: slug })}>
        <Card>
          <CardContent className="p-6">
            <Skeleton className="h-24 w-full" />
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  if (!team) {
    return (
      <PageShell title={tPage("tabTitle")} description={tPage("description", { team: slug })}>
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<UsersIcon className="size-5" />}
              title={tPage("loadError")}
              description=""
            />
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  return (
    <PageShell title={team.name} description={tPage("description", { team: team.slug })}>
      <Card>
        <CardHeader>
          <CardTitle>{tPage("tabTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">{renderMembers(team, grantableRoles)}</CardContent>
      </Card>
    </PageShell>
  );
}
