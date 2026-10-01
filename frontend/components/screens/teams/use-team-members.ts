"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { BULK_ASSIGN_TEAM_MEMBER_ROLES } from "@/graphql/identity/identity.mutations";
import { LIST_ROLES, LIST_TEAM_MEMBERS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkAssignTeamMemberRolesPayload,
  AstroliftMember,
  AstroliftRole,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { selectTeamMembers, localizedTeamMembersList } from "./teams-list";

interface TeamMembersResp {
  astroliftTeamMembers: AstroliftMember[];
}

/**
 * A team's Members tab: its members (the query returns up to 500, unpaged,
 * so the list pages here), the roles a team can grant, and the bulk
 * role-assign mutation (#416 scope B). `team` null (the page is still
 * finding the team) runs nothing.
 */
export function useTeamMembers(team: Pick<AstroliftTeam, "id"> | null) {
  const t = useTranslations("lists.teamMembersBulk");
  const mt = useTranslations("teams.members");
  const perms = useMyPermissions();
  const canManageTeamMembers = perms.can("team.manage_members");
  const list = useListState(localizedTeamMembersList(mt));
  const { state } = list;

  const { data, loading, error, refetch } = useQuery<TeamMembersResp>(LIST_TEAM_MEMBERS, {
    variables: { teamId: team?.id },
    skip: !team,
    fetchPolicy: "cache-and-network",
  });
  const roles = useQuery<{ astroliftRoles: AstroliftRole[] }>(LIST_ROLES, {
    skip: !team || !canManageTeamMembers,
    fetchPolicy: "cache-first",
  });
  // Team-grantable levels only, so the assign dialog never offers a
  // PROJECT or APP role the backend would reject.
  const grantableRoles = (roles.data?.astroliftRoles ?? []).filter(
    (r) => r.scopeLevel === "TEAM" || r.scopeLevel === "ORG"
  );

  const selected = selectTeamMembers(data?.astroliftTeamMembers ?? [], {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const [bulkAssign, { loading: assigning }] = useMutation<{
    bulkAssignAstroliftTeamMemberRoles: MutationResult<AstroliftBulkAssignTeamMemberRolesPayload>;
  }>(BULK_ASSIGN_TEAM_MEMBER_ROLES, {
    refetchQueries: (result) =>
      result.data?.bulkAssignAstroliftTeamMemberRoles.ok && team
        ? [{ query: LIST_TEAM_MEMBERS, variables: { teamId: team.id } }]
        : [],
    onQueryUpdated: (query) => refetchAfterMutation(query, mt("refreshWarning")),
    awaitRefetchQueries: true,
  });

  /**
   * Assign one role to every given member. The mutation is idempotent:
   * members that already had the role come back as `alreadyExisted` and
   * roll into the toast rather than counting as failures. Resolves true when
   * the call went through, so the panel clears its selection.
   */
  async function onAssign(roleId: string, memberIds: string[]): Promise<boolean> {
    if (
      !team ||
      !canManageTeamMembers ||
      memberIds.length === 0 ||
      !roleId ||
      roles.loading ||
      roles.error ||
      roles.data?.astroliftRoles == null ||
      !grantableRoles.some((role) => role.id === roleId)
    )
      return false;
    try {
      const { data: res } = await bulkAssign({
        variables: { input: { teamId: team.id, roleId, memberIds } },
      });
      const env = res?.bulkAssignAstroliftTeamMemberRoles;
      if (!env?.ok) {
        toast.error(
          t("toasts.allFailed", { message: env?.errors?.[0]?.message ?? mt("unknownError") })
        );
        return false;
      }
      if (!env.data) {
        toast.success(mt("accepted"));
        return true;
      }
      const { assignedCount, alreadyAssignedCount, failedCount } = env.data;
      if (failedCount === 0 && alreadyAssignedCount === 0) {
        toast.success(t("toasts.allOk", { count: assignedCount }));
      } else if (failedCount === 0) {
        toast.success(
          t("toasts.mixedIdempotent", { assigned: assignedCount, already: alreadyAssignedCount })
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
      return true;
    } catch (err) {
      toast.error(
        t("toasts.allFailed", {
          message: err instanceof Error && err.message ? err.message : mt("unknownError"),
        })
      );
      return false;
    }
  }

  return {
    list,
    rows: selected.rows,
    totalCount: selected.totalCount,
    loading: !team || (loading && !data),
    error: error
      ? { message: error.message }
      : team && !loading && data?.astroliftTeamMembers == null
        ? { message: t("loadError") }
        : null,
    onRetry: () => {
      void refetch().catch(() => {});
    },
    roles: grantableRoles,
    roleSource: {
      known: roles.data?.astroliftRoles != null,
      loading: roles.loading,
      error: roles.error ? { message: roles.error.message } : null,
      onRetry: () => {
        void roles.refetch().catch(() => {});
      },
    },
    canManageTeamMembers,
    assigning,
    onAssign,
  };
}
