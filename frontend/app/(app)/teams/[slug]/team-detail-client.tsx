"use client";

import { useQuery } from "@apollo/client/react";
import { UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_ROLES, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftRole, AstroliftTeam } from "@/graphql/identity/identity.types";

import { TeamMembersPanel } from "./team-members-panel";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

export function TeamDetailClient({ slug }: { slug: string }) {
  const tPage = useTranslations("lists.teamMembersBulk");
  const teams = useQuery<TeamsResp>(LIST_TEAMS);
  const roles = useQuery<RolesResp>(LIST_ROLES);

  const team = (teams.data?.astroliftTeams ?? []).find((t) => t.slug === slug);

  if (teams.loading && !team) {
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

  // Filter roles down to team-grantable scope levels so the assign
  // dialog can't offer a PROJECT/APP-scoped role that the backend
  // would reject anyway.
  const grantableRoles = (roles.data?.astroliftRoles ?? []).filter(
    (r) => r.scopeLevel === "TEAM" || r.scopeLevel === "ORG"
  );

  return (
    <PageShell title={team.name} description={tPage("description", { team: team.slug })}>
      <Card>
        <CardHeader>
          <CardTitle>{tPage("tabTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <TeamMembersPanel team={team} roles={grantableRoles} />
        </CardContent>
      </Card>
    </PageShell>
  );
}
