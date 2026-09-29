"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import {
  BULK_REVOKE_ROLE_BINDINGS,
  REVOKE_ROLE_BINDING,
} from "@/graphql/identity/identity.mutations";
import { LIST_ROLE_BINDINGS_PAGE, LIST_ROLES } from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkRevokeRoleBindingsPayload,
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { ACCESS_LIST, buildAccessRows, selectAccess, summarizeAccess } from "./principal-access";

/** Whose access: a user by username, or an IdP group by its external id. */
export type AccessSubject =
  | { kind: "user"; userId: string; username: string }
  | { kind: "group"; externalId: string };

/** The server's page ceiling: a principal with more grants than this reads as truncated. */
export const PRINCIPAL_GRANTS_MAX = 200;

/** The bindings page's `holder`: a username, or `group:<external id>`. */
export function holderOf(subject: AccessSubject): string {
  return subject.kind === "user" ? subject.username : `group:${subject.externalId}`;
}

interface BindingsResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}

/**
 * The bindings behind one principal's Access tab, and the revokes. The
 * server holds the list to the principal (`filter: { holder }`), so every
 * grant they hold comes back in one read, up to `PRINCIPAL_GRANTS_MAX`;
 * the tab's `can:` filter, the covered-by reading and the summary need
 * them all in hand, so they are filtered, sorted and paged here, over at
 * most the principal's own grants. `subject` null (the page is still
 * resolving who it is) runs nothing.
 */
export function usePrincipalAccess(subject: AccessSubject | null) {
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");
  const list = useListState(ACCESS_LIST);
  const { state } = list;

  const bindings = useQuery<BindingsResp>(LIST_ROLE_BINDINGS_PAGE, {
    variables: {
      search: null,
      filter: { holder: subject ? [holderOf(subject)] : [] },
      sort: "scope",
      page: 1,
      pageSize: PRINCIPAL_GRANTS_MAX,
    },
    skip: subject === null,
    fetchPolicy: "cache-and-network",
  });
  const roles = useQuery<{ astroliftRoles: AstroliftRole[] }>(LIST_ROLES, { skip: !subject });
  const data = bindings.data ?? bindings.previousData;
  const page = data?.astroliftRoleBindingsPage;

  const rows = React.useMemo(
    () => (subject ? buildAccessRows(page?.items ?? [], roles.data?.astroliftRoles ?? []) : []),
    [subject, page, roles.data]
  );
  const selected = selectAccess(rows, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, { refetchQueries: ["ListRoleBindingsPage"], awaitRefetchQueries: true });
  const [bulkRevoke, { loading: bulkRevoking }] = useMutation<{
    bulkRevokeAstroliftRoleBindings: MutationResult<AstroliftBulkRevokeRoleBindingsPayload>;
  }>(BULK_REVOKE_ROLE_BINDINGS, {
    refetchQueries: ["ListRoleBindingsPage", "ListMembersPage"],
    awaitRefetchQueries: true,
  });

  /** Throws on failure, so the confirm stays open and says why. */
  async function onRevoke(binding: AstroliftRoleBinding): Promise<void> {
    const { data } = await revokeBinding({ variables: { input: { id: binding.id } } });
    if (data?.revokeRoleBinding.ok) toast.success(`Removed ${binding.role.slug}`);
    else throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Remove failed");
  }

  /** Resolves true when any were removed, so the list clears its selection. */
  async function onBulkRevoke(ids: string[]): Promise<boolean> {
    if (ids.length === 0) return false;
    const { data } = await bulkRevoke({ variables: { input: { bindingIds: ids } } });
    const env = data?.bulkRevokeAstroliftRoleBindings;
    if (!env?.ok || !env.data) {
      toast.error(`Nothing was removed: ${env?.errors?.[0]?.message ?? "unknown error"}`);
      return false;
    }
    const { revokedCount, failedCount } = env.data;
    if (failedCount === 0) toast.success(`Removed ${revokedCount}`);
    else toast.warning(`Removed ${revokedCount}; ${failedCount} could not be removed`);
    return true;
  }

  return {
    list,
    rows: selected.rows,
    totalCount: selected.totalCount,
    summary: summarizeAccess(rows),
    loading: subject === null || (bindings.loading && !data) || (roles.loading && !roles.data),
    stale: bindings.loading && !bindings.data && Boolean(data),
    error: bindings.error ? { message: bindings.error.message } : null,
    truncated: (page?.totalCount ?? 0) > PRINCIPAL_GRANTS_MAX,
    onRetry: () => void bindings.refetch(),
    canManage,
    revoking: revoking || bulkRevoking,
    onRevoke,
    onBulkRevoke,
  };
}
