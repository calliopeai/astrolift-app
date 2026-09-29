"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { UPDATE_ROLE } from "@/graphql/identity/identity.mutations";
import { LIST_ROLES } from "@/graphql/identity/identity.queries";
import type { AstroliftRole, MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { roleCatalog } from "./role-catalog";

interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

/** Refreshes the roles list's page by name and the flat list this page reads. */
const ROLE_REFETCH = ["ListRolesPage", { query: LIST_ROLES }];

/**
 * The data half of a role's page (design 3.5): the role, the other roles (to
 * compare against and to duplicate from), the catalog, and the update. There
 * is no single-role query, so it reads the flat roles list, which every tab
 * of the page shares from the cache: the tabs cost no second fetch.
 */
export function useRoleDetail(id: string) {
  const perms = useMyPermissions();
  const { data, loading, error, refetch } = useQuery<RolesResp>(LIST_ROLES, {
    fetchPolicy: "cache-and-network",
  });
  const roles = React.useMemo(() => data?.astroliftRoles ?? [], [data]);
  const role = roles.find((r) => r.id === id) ?? null;
  const catalog = React.useMemo(() => roleCatalog(roles), [roles]);

  const [update, { loading: saving }] = useMutation<{
    updateRole: MutationResult<AstroliftRole>;
  }>(UPDATE_ROLE, { refetchQueries: ROLE_REFETCH, awaitRefetchQueries: true });

  /** Null when saved, or the server's reason, which the tab shows in place. */
  async function save(input: {
    permissions?: string[];
    name?: string;
    description?: string;
  }): Promise<string | null> {
    if (!role) return "This role is no longer there.";
    const { data: res } = await update({
      variables: {
        input: {
          id: role.id,
          ...input,
          name: input.name?.trim(),
          description: input.description?.trim(),
        },
      },
    });
    if (res?.updateRole.ok) {
      toast.success(`Role “${input.name?.trim() || role.name}” saved`);
      return null;
    }
    return res?.updateRole.errors?.[0]?.message ?? "Save failed";
  }

  return {
    id,
    role,
    roles,
    catalog,
    loading: loading && !data,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    canManage: perms.can("org.manage_members"),
    saving,
    savePermissions: (permissions: string[]) => save({ permissions }),
    saveSettings: (s: { name: string; description: string }) => save(s),
  };
}
