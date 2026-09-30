"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import { CREATE_ROLE } from "@/graphql/identity/identity.mutations";
import { LIST_ROLES } from "@/graphql/identity/identity.queries";
import type { AstroliftRole, MutationResult } from "@/graphql/identity/identity.types";

import type { RoleDraft } from "./NewRoleScreen";
import { ROLES_HREF, roleHref } from "./roles-routes";
import { roleCatalog } from "./role-catalog";

interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

/**
 * The data half of NewRoleScreen: every role (to start from, and the
 * catalog), the one named by `?from=`, and the create. On success it opens
 * the new role's page.
 */
export function useNewRole() {
  const router = useRouter();
  const fromId = useSearchParams()?.get("from") ?? null;
  const { data, loading } = useQuery<RolesResp>(LIST_ROLES, { fetchPolicy: "cache-and-network" });
  const roles = React.useMemo(() => data?.astroliftRoles ?? [], [data]);
  const catalog = React.useMemo(() => roleCatalog(roles), [roles]);

  const [create, { loading: creating }] = useMutation<{
    createRole: MutationResult<AstroliftRole>;
  }>(CREATE_ROLE, {
    refetchQueries: ["ListRolesPage", { query: LIST_ROLES }],
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });

  async function onCreate(draft: RoleDraft): Promise<string | null> {
    try {
      const { data: res } = await create({
        variables: {
          input: {
            slug: draft.slug.trim().toLowerCase(),
            name: draft.name.trim(),
            scopeLevel: draft.scopeLevel,
            permissions: draft.permissions,
            description: draft.description.trim(),
            duplicatedFromId: draft.duplicatedFromId,
          },
        },
      });
      const created = res?.createRole;
      if (created?.ok && created.data) {
        toast.success(`Role “${draft.name.trim()}” created`);
        router.push(roleHref(created.data.id));
        return null;
      }
      return created?.errors?.[0]?.message ?? "Create failed";
    } catch (err) {
      return err instanceof Error ? err.message : "Create failed";
    }
  }

  return {
    roles,
    from: roles.find((r) => r.id === fromId) ?? null,
    catalog,
    loading: loading && !data,
    creating,
    onCreate,
    onCancel: () => router.push(fromId ? roleHref(fromId) : ROLES_HREF),
  };
}
