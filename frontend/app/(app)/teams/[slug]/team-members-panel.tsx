"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ShieldPlusIcon, UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { BULK_ASSIGN_TEAM_MEMBER_ROLES } from "@/graphql/identity/identity.mutations";
import { LIST_TEAM_MEMBERS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkAssignTeamMemberRolesPayload,
  AstroliftMember,
  AstroliftRole,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface TeamMembersResp {
  astroliftTeamMembers: AstroliftMember[];
}

interface Props {
  team: Pick<AstroliftTeam, "id" | "slug" | "name">;
  roles: AstroliftRole[];
}

/**
 * Bulk role-assign panel for a single team (#416 scope B).
 *
 * Selection lives in a Set keyed by Member GUID; the sticky footer at
 * the bottom of the viewport surfaces a CTA whenever at least one row
 * is checked. The assign dialog picks a single team-scoped role and
 * applies it to every selected member via
 * `bulkAssignAstroliftTeamMemberRoles`, which is idempotent — members
 * that already had the role come back as `alreadyExisted` and roll up
 * into the post-call toast rather than counting as failures.
 */
export function TeamMembersPanel({ team, roles }: Props) {
  const t = useTranslations("lists.teamMembersBulk");
  const perms = useMyPermissions();
  const canManageTeamMembers = perms.can("team.manage_members");

  const { data, loading, error } = useQuery<TeamMembersResp>(LIST_TEAM_MEMBERS, {
    variables: { teamId: team.id },
    fetchPolicy: "cache-and-network",
  });

  const [selected, setSelected] = React.useState<Set<string>>(() => new Set());
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [roleId, setRoleId] = React.useState<string>("");

  const [bulkAssign, { loading: assigning }] = useMutation<{
    bulkAssignAstroliftTeamMemberRoles: MutationResult<AstroliftBulkAssignTeamMemberRolesPayload>;
  }>(BULK_ASSIGN_TEAM_MEMBER_ROLES, {
    refetchQueries: [{ query: LIST_TEAM_MEMBERS, variables: { teamId: team.id } }],
    awaitRefetchQueries: true,
  });

  const members = data?.astroliftTeamMembers ?? [];

  if (loading && members.length === 0) {
    return (
      <div className="space-y-2 p-6">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <EmptyState icon={<UsersIcon className="size-5" />} title={t("loadError")} description="" />
      </div>
    );
  }

  if (members.length === 0) {
    return (
      <div className="p-6">
        <EmptyState
          icon={<UsersIcon className="size-5" />}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
        />
      </div>
    );
  }

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    const allIds = members.map((m) => m.id);
    setSelected((prev) => {
      const allSelected = allIds.length > 0 && allIds.every((id) => prev.has(id));
      if (allSelected) return new Set();
      return new Set(allIds);
    });
  }

  async function handleAssign() {
    const memberIds = Array.from(selected);
    if (memberIds.length === 0 || !roleId) return;
    try {
      const { data } = await bulkAssign({
        variables: { input: { teamId: team.id, roleId, memberIds } },
      });
      const env = data?.bulkAssignAstroliftTeamMemberRoles;
      if (!env?.ok || !env.data) {
        toast.error(
          t("toasts.allFailed", {
            message: env?.errors?.[0]?.message ?? "unknown error",
          })
        );
        return;
      }
      const { assignedCount, alreadyAssignedCount, failedCount } = env.data;
      if (failedCount === 0 && alreadyAssignedCount === 0) {
        toast.success(t("toasts.allOk", { count: assignedCount }));
      } else if (failedCount === 0) {
        toast.success(
          t("toasts.mixedIdempotent", {
            assigned: assignedCount,
            already: alreadyAssignedCount,
          })
        );
      } else {
        toast.warning(
          t("toasts.partial", {
            assigned: assignedCount,
            already: alreadyAssignedCount,
            failed: failedCount,
          })
        );
      }
      setSelected(new Set());
      setDialogOpen(false);
      setRoleId("");
    } catch (err) {
      toast.error(
        t("toasts.allFailed", {
          message: err instanceof Error ? err.message : "unknown error",
        })
      );
    }
  }

  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            {canManageTeamMembers && (
              <TableHead className="w-10">
                <input
                  type="checkbox"
                  aria-label={t("selectAllLabel")}
                  checked={members.length > 0 && members.every((m) => selected.has(m.id))}
                  onChange={toggleAll}
                  className="size-4"
                />
              </TableHead>
            )}
            <TableHead>{t("columns.user")}</TableHead>
            <TableHead>{t("columns.lifecycle")}</TableHead>
            <TableHead>{t("columns.joined")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {members.map((m) => (
            <TableRow key={m.id}>
              {canManageTeamMembers && (
                <TableCell>
                  <input
                    type="checkbox"
                    aria-label={t("selectRowLabel", { user: m.user.username })}
                    checked={selected.has(m.id)}
                    onChange={() => toggleOne(m.id)}
                    className="size-4"
                    disabled={assigning}
                  />
                </TableCell>
              )}
              <TableCell>
                <div className="font-medium">{m.user.username}</div>
                <div className="text-muted-foreground text-xs">{m.user.email}</div>
              </TableCell>
              <TableCell>
                <Badge variant={m.isActive ? "default" : "secondary"}>{m.lifecycle}</Badge>
              </TableCell>
              <TableCell className="text-muted-foreground text-sm">
                {m.joinedAt
                  ? new Date(m.joinedAt).toLocaleDateString()
                  : new Date(m.createdAt).toLocaleDateString()}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {canManageTeamMembers && selected.size > 0 && (
        <div className="bg-background pointer-events-auto fixed inset-x-0 bottom-0 z-30 border-t shadow-lg">
          <div className="mx-auto flex max-w-5xl flex-col items-stretch gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm font-medium">{t("selected", { count: selected.size })}</p>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Button
                variant="ghost"
                onClick={() => setSelected(new Set())}
                disabled={assigning}
                className="min-h-11 w-full sm:w-auto"
              >
                {t("clear")}
              </Button>
              <Button
                onClick={() => {
                  setRoleId("");
                  setDialogOpen(true);
                }}
                disabled={assigning}
                className="min-h-11 w-full sm:w-auto"
              >
                <ShieldPlusIcon className="size-4" />
                {t("assignButton", { count: selected.size })}
              </Button>
            </div>
          </div>
        </div>
      )}

      <AlertDialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("assignDialog.title", { count: selected.size })}</AlertDialogTitle>
            <AlertDialogDescription>
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
              onClick={handleAssign}
            >
              {t("assignDialog.confirmLabel")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
