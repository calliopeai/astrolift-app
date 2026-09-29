"use client";

import { ShieldPlusIcon, UserPlusIcon, UsersIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { PrincipalChip } from "@/components/access/PrincipalChip";
import type { Column, RowSelection } from "@/components/data-table";
import { DetailStatusBadge, type Dot } from "@/components/detail/EntityDetailShell";
import { ListPage } from "@/components/list/ListPage";
import {
  grantHref,
  PEOPLE_HREF,
  TEAMS_HREF,
} from "@/components/screens/administration/access/access-nav";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AstroliftMember, AstroliftTeam } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useTeamMembers } from "./use-team-members";

export type TeamMembersPanelProps = ReturnType<typeof useTeamMembers> & {
  team: Pick<AstroliftTeam, "id" | "slug" | "name">;
};

const TONE: Record<string, Dot> = { active: "ok", invited: "warn", suspended: "warn" };

/**
 * A team's Members tab (access UX design 3.2): who is on the team, one
 * numbered list. A row opens the person. Add member is the grant page with
 * the team as the scope (a grant at a team's scope is what puts a person on
 * it); selecting rows offers Assign role, which grants one team-grantable
 * role to each (#416 scope B). Pure: data from useTeamMembers.
 */
export function TeamMembersPanel({
  team,
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  roles,
  canManageTeamMembers,
  assigning,
  onAssign,
}: TeamMembersPanelProps) {
  const t = useTranslations("lists.teamMembersBulk");
  const fmt = useFormatters();
  const [target, setTarget] = React.useState<RowSelection | null>(null);
  const [roleId, setRoleId] = React.useState("");
  const count = target?.selectedCount ?? 0;

  const columns: Column<AstroliftMember>[] = [
    {
      id: "user",
      header: t("columns.user"),
      sortKey: "user",
      cellClassName: "max-w-80",
      cell: (m) => (
        <PrincipalChip
          principal={{ kind: "user", id: m.user.id, name: m.user.username, detail: m.user.email }}
          variant="block"
          showId={false}
        />
      ),
    },
    {
      id: "lifecycle",
      header: t("columns.lifecycle"),
      cell: (m) => <DetailStatusBadge status={m.lifecycle} tone={TONE[m.lifecycle] ?? "muted"} />,
    },
    {
      id: "joined",
      header: t("columns.joined"),
      sortKey: "joined",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (m) => fmt.formatDate(m.joinedAt ?? m.createdAt),
    },
  ];

  const addHref = grantHref({
    scope: { kind: "TEAM", id: team.id, name: team.slug },
    returnTo: `${TEAMS_HREF}/${encodeURIComponent(team.slug)}/members`,
  });

  async function handleAssign() {
    if (!target) return;
    if (await onAssign(roleId, target.selectedIds)) {
      target.clear();
      setTarget(null);
      setRoleId("");
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {canManageTeamMembers && (
        <div className="flex min-w-0 justify-end">
          <Button size="sm" variant="outline" asChild>
            <Link href={addHref}>
              <UserPlusIcon className="size-4" />
              Add member
            </Link>
          </Button>
        </div>
      )}
      <ListPage<AstroliftMember>
        embedded
        list={list}
        label="Members"
        columns={columns}
        rows={rows}
        getRowId={(m) => m.id}
        rowHref={(m) => `${PEOPLE_HREF}/${m.id}`}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        bulkActions={
          canManageTeamMembers
            ? (selection) => (
                <Button
                  size="sm"
                  disabled={assigning}
                  onClick={() => {
                    setRoleId("");
                    setTarget(selection);
                  }}
                >
                  <ShieldPlusIcon className="size-4" />
                  {t("assignButton", { count: selection.selectedCount })}
                </Button>
              )
            : undefined
        }
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: t("emptyTitle"),
          description: "Grant someone a role at this team's scope to put them on it.",
          ...(canManageTeamMembers ? { actionHref: addHref, actionLabel: "Add member" } : {}),
        }}
      />

      <AlertDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next && !assigning) setTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("assignDialog.title", { count })}</AlertDialogTitle>
            <AlertDialogDescription className="[overflow-wrap:anywhere]">
              {t("assignDialog.description", { team: team.slug })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="bulk-role-select">
              {t("assignDialog.roleLabel")}
            </label>
            {roles.length === 0 ? (
              <p className="text-muted-foreground text-xs">{t("assignDialog.noRoles")}</p>
            ) : (
              <Select value={roleId} onValueChange={setRoleId}>
                <SelectTrigger id="bulk-role-select">
                  <SelectValue placeholder={t("assignDialog.rolePlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {roles.map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      <span className="font-medium">{r.name}</span>
                      <span className="text-muted-foreground ml-2 font-mono text-xs">{r.slug}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={assigning}>{t("assignDialog.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              disabled={assigning || !roleId || roles.length === 0}
              onClick={(e) => {
                e.preventDefault();
                void handleAssign();
              }}
            >
              {t("assignDialog.confirmLabel")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
