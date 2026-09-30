"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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
  const { org } = useActiveOrg();
  const teams = useQuery<{ astroliftTeams: AstroliftTeam[] }>(LIST_TEAMS);
  const projects = useQuery<{ astroliftProjects: AstroliftProject[] }>(LIST_PROJECTS);

  const [grantRole, { loading }] = useMutation<{
    grantRole: MutationResult<AstroliftRoleBinding>;
  }>(GRANT_ROLE, {
    refetchQueries: ["ListRoleBindingsPage", "ListMembersPage"],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the grant succeeded, so the sheet can close. */
  async function onGrant(input: GrantRoleInput): Promise<boolean> {
    const { data } = await grantRole({ variables: { input } });
    if (data?.grantRole.ok) {
      toast.success("Role granted");
      return true;
    }
    toast.error(data?.grantRole.errors?.[0]?.message ?? "Grant failed");
    return false;
  }

  return {
    org,
    teams: teams.data?.astroliftTeams,
    projects: projects.data?.astroliftProjects,
    granting: loading,
    onGrant,
  };
}
