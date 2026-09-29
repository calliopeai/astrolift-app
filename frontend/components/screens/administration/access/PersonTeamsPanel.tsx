"use client";

import { UsersIcon } from "lucide-react";

import { DetailStatusBadge, type Dot } from "@/components/detail/EntityDetailShell";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { useFormatters } from "@/lib/i18n/formatters";

import { TEAMS_HREF } from "./access-nav";
import type { TeamMembershipRow } from "./use-person-teams";

export interface PersonTeamsPanelProps {
  list: ListStateController;
  rows: TeamMembershipRow[];
  totalCount: number;
  loading: boolean;
  stale?: boolean;
  error: { message: string } | null;
  onRetry: () => void;
}

const TONE: Record<string, Dot> = { active: "ok", invited: "warn", suspended: "warn" };

/**
 * A person's Teams tab (access UX design 3.2): the teams they are on. A
 * team opens its own page, where what being on it gives is its Access tab.
 * Pure: data from usePersonTeams.
 */
export function PersonTeamsPanel({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: PersonTeamsPanelProps) {
  const fmt = useFormatters();
  const columns: Column<TeamMembershipRow>[] = [
    {
      id: "team",
      header: "Team",
      sortKey: "team",
      cellClassName: "max-w-80",
      cell: (r) =>
        r.slug ? (
          <span className="block min-w-0 truncate font-mono text-sm" title={r.slug}>
            {r.slug}
          </span>
        ) : (
          <span
            className="text-muted-foreground block min-w-0 truncate font-mono text-sm"
            title="Named once someone holds a role on this team"
          >
            team #{r.pk}
          </span>
        ),
    },
    {
      id: "membership",
      header: "Membership",
      cell: (r) => (
        <DetailStatusBadge status={r.member.lifecycle} tone={TONE[r.member.lifecycle] ?? "muted"} />
      ),
    },
    {
      id: "joined",
      header: "Joined",
      sortKey: "joined",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (r) => fmt.formatDate(r.member.joinedAt ?? r.member.createdAt),
    },
  ];

  return (
    <ListPage<TeamMembershipRow>
      embedded
      list={list}
      label="Teams"
      columns={columns}
      rows={rows}
      getRowId={(r) => r.member.id}
      rowHref={(r) => (r.slug ? `${TEAMS_HREF}/${encodeURIComponent(r.slug)}` : TEAMS_HREF)}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      totalCount={totalCount}
      empty={{
        icon: <UsersIcon className="size-5" />,
        title: "Not on a team",
        description: "Granting a role at a team's scope puts a person on that team.",
      }}
    />
  );
}
