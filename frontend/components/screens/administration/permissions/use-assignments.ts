"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { useCursorTable, useRowSelection, type CursorPage } from "@/components/data-table";
import {
  BULK_REVOKE_ROLE_BINDINGS,
  REVOKE_ROLE_BINDING,
} from "@/graphql/identity/identity.mutations";
import {
  LIST_ROLE_BINDINGS,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_ROLES,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkRevokeRoleBindingsPayload,
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface BindingsPageResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

/**
 * Both revoke paths refresh two documents: this table's paginated one by
 * operation name, so it re-runs with the cursor and search currently in
 * effect, and the deprecated flat list that /members, the member detail
 * page and the diagnostics tab still read from the cache.
 */
const REVOKE_REFETCH = ["ListRoleBindingsPage", { query: LIST_ROLE_BINDINGS }];

/** The data half of AssignmentsView: the role-bindings page, the roles, and revoke. */
export function useAssignments() {
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");

  // `astroliftRoleBindingsPage` searches username, email, first / last
  // name, group external id and role slug / name. It takes no sort
  // argument, so no column declares a `sortKey`.
  const table = useCursorTable<AstroliftRoleBinding>({
    query: LIST_ROLE_BINDINGS_PAGE,
    extract: (d) => (d as BindingsPageResp | undefined)?.astroliftRoleBindingsPage,
    searchVariable: "search",
    urlKey: "rb",
    fetchPolicy: "cache-and-network",
  });
  const selection = useRowSelection();

  const roles = useQuery<RolesResp>(LIST_ROLES);

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: REVOKE_REFETCH,
    awaitRefetchQueries: true,
  });
  const [bulkRevoke, { loading: bulkRevoking }] = useMutation<{
    bulkRevokeAstroliftRoleBindings: MutationResult<AstroliftBulkRevokeRoleBindingsPayload>;
  }>(BULK_REVOKE_ROLE_BINDINGS, {
    refetchQueries: REVOKE_REFETCH,
    awaitRefetchQueries: true,
  });

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onRevoke(b: AstroliftRoleBinding): Promise<void> {
    const { data } = await revokeBinding({ variables: { input: { id: b.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  /** Revokes the selected bindings; the view closes its confirm when this settles. */
  async function onBulkRevoke(): Promise<void> {
    const ids = selection.selectedIds;
    if (ids.length === 0) return;
    const { data } = await bulkRevoke({ variables: { input: { bindingIds: ids } } });
    const env = data?.bulkRevokeAstroliftRoleBindings;
    if (!env?.ok || !env.data) {
      toast.error(env?.errors?.[0]?.message ?? "Bulk revoke failed");
      return;
    }
    const { revokedCount, failedCount } = env.data;
    if (failedCount === 0) {
      toast.success(`Revoked ${revokedCount} binding${revokedCount === 1 ? "" : "s"}`);
    } else {
      toast.warning(`Revoked ${revokedCount}, ${failedCount} failed`);
    }
    selection.clear();
  }

  return {
    table,
    selection,
    canManage,
    roles: roles.data?.astroliftRoles ?? [],
    rolesLoading: roles.loading,
    revoking,
    bulkRevoking,
    onRevoke,
    onBulkRevoke,
  };
}
