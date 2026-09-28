"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { BULK_ASSIGN_TEAM_MEMBER_ROLES } from "@/graphql/identity/identity.mutations";
import { LIST_TEAM_MEMBERS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkAssignTeamMemberRolesPayload,
  AstroliftMember,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface TeamMembersResp {
  astroliftTeamMembers: AstroliftMember[];
}

/**
 * A team's members and the bulk role-assign mutation (#416 scope B). The data
 * half of TeamMembersPanel.
 */
export function useTeamMembers(team: Pick<AstroliftTeam, "id">) {
  const t = useTranslations("lists.teamMembersBulk");
  const perms = useMyPermissions();
  const canManageTeamMembers = perms.can("team.manage_members");

  const { data, loading, error } = useQuery<TeamMembersResp>(LIST_TEAM_MEMBERS, {
    variables: { teamId: team.id },
    fetchPolicy: "cache-and-network",
  });

  const [bulkAssign, { loading: assigning }] = useMutation<{
    bulkAssignAstroliftTeamMemberRoles: MutationResult<AstroliftBulkAssignTeamMemberRolesPayload>;
  }>(BULK_ASSIGN_TEAM_MEMBER_ROLES, {
    refetchQueries: [{ query: LIST_TEAM_MEMBERS, variables: { teamId: team.id } }],
    awaitRefetchQueries: true,
  });

  /**
   * Assign one role to every given member. `bulkAssignAstroliftTeamMemberRoles`
   * is idempotent: members that already had the role come back as
   * `alreadyExisted` and roll up into the toast rather than counting as
   * failures. Resolves true when the call went through, so the panel clears
   * its selection and closes the dialog.
   */
  async function onAssign(roleId: string, memberIds: string[]): Promise<boolean> {
    if (memberIds.length === 0 || !roleId) return false;
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
        return false;
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
      return true;
    } catch (err) {
      toast.error(
        t("toasts.allFailed", {
          message: err instanceof Error ? err.message : "unknown error",
        })
      );
      return false;
    }
  }

  return {
    members: data?.astroliftTeamMembers ?? [],
    loading,
    error,
    canManageTeamMembers,
    assigning,
    onAssign,
  };
}
