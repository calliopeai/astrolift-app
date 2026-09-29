"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { useWalk } from "@/components/screens/members/use-walk";
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

/**
 * Whose access: a user or an IdP group by what the bindings search matches
 * (`search`) and what a binding must hold exactly (`holds`), or a team by
 * its scope label (every binding held at the team).
 */
export type AccessSubject =
  | { kind: "user"; userId: string; username: string }
  | { kind: "group"; externalId: string }
  | { kind: "team"; slug: string };

function holds(subject: AccessSubject, b: AstroliftRoleBinding): boolean {
  switch (subject.kind) {
    case "user":
      return b.user?.id === subject.userId;
    case "group":
      return !b.user && b.groupExternalId === subject.externalId;
    case "team":
      return b.scopeKind === "TEAM" && b.sourceScopeLabel === `team ${subject.slug}`;
  }
}

/**
 * The bindings behind one principal's (or one team's) Access tab, and the
 * revokes. There is no per-principal binding query (design 6.7), so the
 * org's bindings page is walked with the principal's name as its search,
 * then held to the exact principal; a team's are every binding whose scope
 * label is that team, which the search cannot narrow (it matches users,
 * groups and roles, not scopes). `subject` null (the page is still
 * resolving who it is) runs nothing.
 */
export function usePrincipalAccess(subject: AccessSubject | null) {
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");
  const list = useListState(ACCESS_LIST);
  const { state } = list;

  const search =
    subject?.kind === "user"
      ? subject.username
      : subject?.kind === "group"
        ? subject.externalId
        : null;
  const walk = useWalk<AstroliftRoleBinding>(
    LIST_ROLE_BINDINGS_PAGE,
    (d) =>
      (d as { astroliftRoleBindingsPage?: CursorPage<AstroliftRoleBinding> } | undefined)
        ?.astroliftRoleBindingsPage,
    { variables: { search }, skip: subject === null }
  );
  const roles = useQuery<{ astroliftRoles: AstroliftRole[] }>(LIST_ROLES, { skip: !subject });

  const rows = React.useMemo(
    () =>
      subject
        ? buildAccessRows(
            walk.rows.filter((b) => holds(subject, b)),
            roles.data?.astroliftRoles ?? []
          )
        : [],
    [subject, walk.rows, roles.data]
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
    loading: subject === null || walk.loading || (roles.loading && !roles.data),
    stale: walk.stale,
    error: walk.error,
    truncated: walk.truncated,
    onRetry: walk.refetch,
    canManage,
    revoking: revoking || bulkRevoking,
    onRevoke,
    onBulkRevoke,
  };
}
