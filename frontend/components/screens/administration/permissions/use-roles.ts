"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { CREATE_ROLE, UPDATE_ROLE } from "@/graphql/identity/identity.mutations";
import { LIST_ROLES, LIST_ROLES_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftRole, MutationResult, ScopeKind } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface RolesResp {
  astroliftRoles: AstroliftRole[];
}
interface RolesPageResp {
  astroliftRolesPage: CursorPage<AstroliftRole>;
}

/** What the editor sheet submits; the hook trims and normalises it. */
export interface RoleDraft {
  slug: string;
  name: string;
  description: string;
  scopeLevel: ScopeKind;
  /** Sorted permission slugs. */
  permissions: string[];
}

/**
 * Both editor paths refresh two documents: this table's paginated one by
 * operation name, so it re-runs with the cursor and search currently in
 * effect, and the flat list the permission catalogue below is derived
 * from and that the assignments tab reads for its grant dialog.
 */
const ROLE_REFETCH = ["ListRolesPage", { query: LIST_ROLES }];

/** The data half of RolesView: the roles page, the catalogue, and the editor's mutations. */
export function useRoles() {
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");

  // `astroliftRolesPage` searches slug, name and description. It takes no
  // sort argument, so no column declares a `sortKey`.
  const table = useCursorTable<AstroliftRole>({
    query: LIST_ROLES_PAGE,
    extract: (d) => (d as RolesPageResp | undefined)?.astroliftRolesPage,
    searchVariable: "search",
    urlKey: "role",
    fetchPolicy: "cache-and-network",
  });

  // The flat list stays, and it is not the table's data source: the
  // permission checklist below is the union of *every* role's permission
  // set, so deriving it from whichever page is on screen would silently
  // shrink the editor's catalogue as an operator paged or searched. The
  // `org_owner` system role carries the full `Permission` enum (backend
  // `system_roles.py`), so the union over the whole list is the complete
  // catalogue, with no dedicated catalogue query to drift ahead of what
  // the backend will accept.
  const { data } = useQuery<RolesResp>(LIST_ROLES, { fetchPolicy: "cache-and-network" });
  const allPermissions = React.useMemo(() => {
    const set = new Set<string>();
    for (const r of data?.astroliftRoles ?? []) for (const p of r.permissions) set.add(p);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [data]);

  const [createRoleMutation, { loading: creating }] = useMutation<{
    createRole: MutationResult<AstroliftRole>;
  }>(CREATE_ROLE, {
    refetchQueries: ROLE_REFETCH,
    awaitRefetchQueries: true,
  });
  const [updateRoleMutation, { loading: updating }] = useMutation<{
    updateRole: MutationResult<AstroliftRole>;
  }>(UPDATE_ROLE, {
    refetchQueries: ROLE_REFETCH,
    awaitRefetchQueries: true,
  });

  /** Resolves true when the role was created, so the sheet can close. */
  async function createRole(draft: RoleDraft): Promise<boolean> {
    const { data } = await createRoleMutation({
      variables: {
        input: {
          slug: draft.slug.trim().toLowerCase(),
          name: draft.name.trim(),
          scopeLevel: draft.scopeLevel,
          permissions: draft.permissions,
          description: draft.description.trim(),
        },
      },
    });
    if (data?.createRole.ok) {
      toast.success(`Role “${draft.name.trim()}” created`);
      return true;
    }
    toast.error(data?.createRole.errors?.[0]?.message ?? "Create failed");
    return false;
  }

  /** Resolves true when the role was updated, so the sheet can close. */
  async function updateRole(id: string, draft: RoleDraft): Promise<boolean> {
    const { data } = await updateRoleMutation({
      variables: {
        input: {
          id,
          name: draft.name.trim(),
          description: draft.description.trim(),
          permissions: draft.permissions,
        },
      },
    });
    if (data?.updateRole.ok) {
      toast.success(`Role “${draft.name.trim()}” updated`);
      return true;
    }
    toast.error(data?.updateRole.errors?.[0]?.message ?? "Update failed");
    return false;
  }

  return {
    table,
    allPermissions,
    canManage,
    createRole,
    updateRole,
    saving: creating || updating,
  };
}
