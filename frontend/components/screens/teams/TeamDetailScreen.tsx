"use client";

import { ShieldPlusIcon } from "lucide-react";
import Link from "next/link";
import type * as React from "react";

import {
  accessCrumbs,
  grantHref,
  TEAMS_HREF,
} from "@/components/screens/administration/access/access-nav";
import { PrincipalPage } from "@/components/screens/administration/access/PrincipalPage";
import { type TeamTab, teamTabs } from "@/components/screens/administration/access/principal-tabs";
import { Button } from "@/components/ui/button";

import type { useTeamDetail } from "./use-team-detail";

export type TeamDetailScreenProps = ReturnType<typeof useTeamDetail> & {
  tab: TeamTab;
  /** The active tab's body, fed by that tab's own hook. */
  children: React.ReactNode;
};

/**
 * A team's page (access UX design 3.2) on the detail archetype: what being
 * on the team gives (Access: every grant held at the team, and what it
 * reaches) and who is on it (Members). Grant access opens the grant page
 * with the team as the scope. Pure.
 */
export function TeamDetailScreen({
  slug,
  team,
  canManage,
  loading,
  error,
  onRetry,
  tab,
  children,
}: TeamDetailScreenProps) {
  const tabs = teamTabs(slug, tab);
  return (
    <PrincipalPage
      crumbs={accessCrumbs("teams", team?.name ?? slug)}
      principal={team ? { kind: "team", id: team.slug, name: team.name } : null}
      fallbackTitle={slug}
      context={team ? <span className="font-mono">{team.slug}</span> : undefined}
      primaryAction={
        team && canManage ? (
          <Button size="sm" asChild>
            <Link
              href={grantHref({
                scope: { kind: "TEAM", id: team.id, name: team.slug },
                returnTo: tabs.find((t) => t.active)?.href ?? TEAMS_HREF,
              })}
            >
              <ShieldPlusIcon className="size-4" />
              Grant access
            </Link>
          </Button>
        ) : undefined
      }
      tabs={tabs}
      loading={loading}
      error={error}
      onRetry={onRetry}
      notFound={!loading && !error && !team}
      notFoundCopy={{
        title: "Team not found",
        description:
          "This team may not exist, may have been deleted, or you may not have access to it.",
        backHref: TEAMS_HREF,
        backLabel: "Back to Teams",
      }}
    >
      {children}
    </PrincipalPage>
  );
}
