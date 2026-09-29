"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { REVOKE_ROLE_BINDING } from "@/graphql/identity/identity.mutations";
import { LIST_ROLE_BINDINGS, LIST_ROLE_BINDINGS_PAGE } from "@/graphql/identity/identity.queries";
import type {
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { type ListPageData, useListPageQuery } from "../access/use-list-page-query";
import { ROLE_HOLDERS_LIST } from "./permissions-lists";

interface BindingsPageResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}

/** Revoke refreshes this list's page and the flat list the member pages still read. */
const REVOKE_REFETCH = ["ListRoleBindingsPage", { query: LIST_ROLE_BINDINGS }];

/**
 * The data half of RoleHoldersTab: who holds `role`, and revoke. Mounted only
 * on the Holders tab, so the bindings query runs only there.
 *
 * `astroliftRoleBindingsPage` has no role argument. It searches role slugs,
 * so the page is asked for the slug (or the person's own search) and the
 * rows of other roles that match the text are dropped. The count is then the
 * search's, not the role's, so it is not shown.
 */
export function useRoleHolders(role: Pick<AstroliftRole, "id" | "slug">) {
  const perms = useMyPermissions();
  const list = useListState(ROLE_HOLDERS_LIST);
  const searched = useListPageQuery<AstroliftRoleBinding>(
    LIST_ROLE_BINDINGS_PAGE,
    { ...list, state: { ...list.state, q: list.state.q.trim() || role.slug } },
    (d) => (d as BindingsPageResp | undefined)?.astroliftRoleBindingsPage
  );
  const page: ListPageData<AstroliftRoleBinding> = {
    ...searched,
    rows: searched.rows.filter((b) => b.role.id === role.id),
    totalCount: null,
  };

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, { refetchQueries: REVOKE_REFETCH, awaitRefetchQueries: true });

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onRevoke(b: AstroliftRoleBinding): Promise<void> {
    const { data } = await revokeBinding({ variables: { input: { id: b.id } } });
    if (data?.revokeRoleBinding.ok) toast.success("Role revoked");
    else throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
  }

  return { list, page, canManage: perms.can("org.manage_members"), revoking, onRevoke };
}
