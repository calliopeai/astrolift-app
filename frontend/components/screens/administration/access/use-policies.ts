"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { SOFT_DELETE_POLICY } from "@/graphql/identity/identity.mutations";
import { LIST_POLICIES, LIST_POLICIES_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftPolicy, MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { POLICIES_LIST } from "./policies-list";
import { useListPageQuery } from "./use-list-page-query";

interface PoliciesPageResp {
  astroliftPoliciesPage: CursorPage<AstroliftPolicy>;
}

/** The data half of PoliciesScreen: the policy list (URL state) and soft-delete. */
export function usePolicies() {
  const perms = useMyPermissions();
  const list = useListState(POLICIES_LIST);
  const page = useListPageQuery<AstroliftPolicy>(
    LIST_POLICIES_PAGE,
    list,
    (d) => (d as PoliciesPageResp | undefined)?.astroliftPoliciesPage
  );

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeletePolicy: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_POLICY, {
    // LIST_POLICIES still backs the other policy surface; "ListPoliciesPage"
    // is this list's own walk, which is a different root field and would
    // otherwise keep showing the deleted row.
    refetchQueries: [{ query: LIST_POLICIES }, "ListPoliciesPage"],
    awaitRefetchQueries: true,
  });

  /** Throws on failure so the confirm dialog shows the error inline. */
  async function deletePolicy(p: AstroliftPolicy) {
    const { data } = await softDelete({ variables: { input: { id: p.id } } });
    if (data?.softDeletePolicy.ok) {
      toast.success(`Deleted ${p.slug}`);
    } else {
      throw new Error(data?.softDeletePolicy.errors?.[0]?.message ?? "Delete failed");
    }
  }

  return { list, page, canManage: perms.can("org.update"), deleting, deletePolicy };
}
