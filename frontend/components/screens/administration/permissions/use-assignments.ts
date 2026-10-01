"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import type { CursorPage } from "@/components/data-table";
import { useCsvExport } from "@/components/list/use-csv-export";
import { useListState } from "@/components/list/use-list-state";
import {
  BULK_REVOKE_ROLE_BINDINGS,
  REVOKE_ROLE_BINDING,
} from "@/graphql/identity/identity.mutations";
import {
  EXPORT_ROLE_BINDINGS_CSV,
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

import { numberedVariables } from "../access/identity-lists";
import { useNumberedListQuery } from "../access/use-list-page-query";
import { ASSIGNMENTS_LIST, bindingsFilter } from "./permissions-lists";

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
  const client = useApolloClient();
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");

  // `astroliftRoleBindingsPage` searches username, email, first / last
  // name, group external id and role slug / name, and filters, sorts and
  // numbers the pages (#2153).
  const list = useListState(ASSIGNMENTS_LIST);
  const page = useNumberedListQuery<AstroliftRoleBinding, ReturnType<typeof bindingsFilter>>(
    LIST_ROLE_BINDINGS_PAGE,
    list,
    (d) => (d as BindingsPageResp | undefined)?.astroliftRoleBindingsPage,
    { toFilter: bindingsFilter }
  );

  const { exportingCsv, onExportCsv } = useCsvExport(async () => {
    const variables = numberedVariables(
      { ...list.state, q: list.state.q },
      bindingsFilter(list.filters)
    );
    const result = await client.query<{
      astroliftRoleBindingsCsv: { filename: string; content: string };
    }>({
      query: EXPORT_ROLE_BINDINGS_CSV,
      variables: { search: variables.search, filter: variables.filter, sort: variables.sort },
      fetchPolicy: "network-only",
    });
    if (!result.data?.astroliftRoleBindingsCsv) throw new Error("The export returned no file");
    return result.data.astroliftRoleBindingsCsv;
  });

  const roles = useQuery<RolesResp>(LIST_ROLES);

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: REVOKE_REFETCH,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });
  const [bulkRevoke, { loading: bulkRevoking }] = useMutation<{
    bulkRevokeAstroliftRoleBindings: MutationResult<AstroliftBulkRevokeRoleBindingsPayload>;
  }>(BULK_REVOKE_ROLE_BINDINGS, {
    refetchQueries: REVOKE_REFETCH,
    onQueryUpdated: refetchAfterMutation,
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

  /**
   * Revokes the selected bindings. Resolves true when any were revoked, so the
   * view clears its selection. Total failure rejects so its confirm stays open.
   */
  async function onBulkRevoke(ids: string[]): Promise<boolean> {
    if (ids.length === 0) return false;
    const { data } = await bulkRevoke({ variables: { input: { bindingIds: ids } } });
    const env = data?.bulkRevokeAstroliftRoleBindings;
    if (!env?.ok || !env.data) {
      throw new Error(env?.errors?.[0]?.message ?? "Bulk revoke failed");
    }
    const { revokedCount, failedCount } = env.data;
    if (revokedCount === 0 && failedCount > 0) throw new Error(`All ${failedCount} revokes failed`);
    if (failedCount === 0) {
      toast.success(`Revoked ${revokedCount} binding${revokedCount === 1 ? "" : "s"}`);
    } else {
      toast.warning(`Revoked ${revokedCount}, ${failedCount} failed`);
    }
    return true;
  }

  return {
    list,
    page,
    canManage,
    roles: roles.data?.astroliftRoles ?? [],
    rolesLoading: roles.loading,
    rolesKnown: roles.data?.astroliftRoles != null,
    rolesError: roles.error,
    onRetryRoles: async () => {
      try {
        await roles.refetch();
      } catch {
        /* The query preserves its read error. */
      }
    },
    revoking,
    bulkRevoking,
    onRevoke,
    onBulkRevoke,
    exportingCsv,
    onExportCsv,
  };
}
