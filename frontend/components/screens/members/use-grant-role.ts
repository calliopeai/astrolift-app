"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GRANT_ROLE } from "@/graphql/identity/identity.mutations";
import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftProject,
  AstroliftRoleBinding,
  AstroliftTeam,
  MutationResult,
  ScopeKind,
} from "@/graphql/identity/identity.types";

export interface GrantRoleInput {
  userId: string;
  roleId: string;
  scopeKind: ScopeKind;
  scopeGuid: string;
}

/**
 * The data half of GrantRoleSheet: the active org, the team and project
 * lists the scope picker offers, and the grant mutation.
 */
export function useGrantRole() {
  const t = useTranslations("shared.access.legacyGrantRole");
  const organization = useActiveOrg();
  const { org } = organization;
  const teams = useQuery<{ astroliftTeams: AstroliftTeam[] }>(LIST_TEAMS, {
    skip: !org,
    fetchPolicy: "cache-first",
  });
  const projects = useQuery<{ astroliftProjects: AstroliftProject[] }>(LIST_PROJECTS, {
    skip: !org,
    fetchPolicy: "cache-first",
  });

  const [grantRole, { loading }] = useMutation<{
    grantRole: MutationResult<AstroliftRoleBinding>;
  }>(GRANT_ROLE, {
    refetchQueries: (result) =>
      result.data?.grantRole.ok ? ["ListRoleBindingsPage", "ListMembersPage"] : [],
    onQueryUpdated: (query) => refetchAfterMutation(query, t("refreshWarning")),
    awaitRefetchQueries: true,
  });

  /** Resolves true when the grant succeeded, so the sheet can close. */
  async function onGrant(input: GrantRoleInput): Promise<boolean> {
    if (!org || organization.loading || organization.error) return false;
    try {
      const { data } = await grantRole({ variables: { input } });
      if (data?.grantRole.ok) {
        toast.success(t("granted"));
        return true;
      }
      toast.error(data?.grantRole.errors?.[0]?.message ?? t("failed"));
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t("failed"));
    }
    return false;
  }

  return {
    org,
    teams: teams.data?.astroliftTeams,
    projects: projects.data?.astroliftProjects,
    organizationState: { loading: organization.loading, error: organization.error },
    scopeReads: {
      TEAM: {
        loading: teams.loading,
        error: teams.error,
        known: teams.data?.astroliftTeams != null,
      },
      PROJECT: {
        loading: projects.loading,
        error: projects.error,
        known: projects.data?.astroliftProjects != null,
      },
    },
    onRetryScope: async (kind: ScopeKind) => {
      try {
        if (kind === "TEAM") await teams.refetch();
        else if (kind === "PROJECT") await projects.refetch();
      } catch {
        /* Query state retains the original diagnostic. */
      }
    },
    granting: loading,
    onGrant,
  };
}
