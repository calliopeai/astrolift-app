"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { CREATE_POLICY, SOFT_DELETE_POLICY } from "@/graphql/identity/identity.mutations";
import { LIST_POLICIES, LIST_POLICIES_PAGE } from "@/graphql/identity/identity.queries";
import type {
  AstroliftPolicy,
  MutationResult,
  PolicyEffect,
  ScopeKind,
} from "@/graphql/identity/identity.types";

interface PoliciesPageResp {
  astroliftPoliciesPage: CursorPage<AstroliftPolicy>;
}

export interface CreatePolicyInput {
  name: string;
  slug: string;
  scopeLevel: ScopeKind;
  effect: PolicyEffect;
  actionPattern: string;
  conditions: unknown;
}

/** The data half of PoliciesScreen: the policy walk plus create and soft-delete. */
export function usePolicies() {
  // `astroliftPoliciesPage` takes `search`, `limit` and `after` only — it has
  // no sort argument, so no column declares a `sortKey`.
  const table = useCursorTable<AstroliftPolicy>({
    query: LIST_POLICIES_PAGE,
    extract: (d) => (d as PoliciesPageResp | undefined)?.astroliftPoliciesPage,
    searchVariable: "search",
    urlKey: "pol",
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeletePolicy: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_POLICY, {
    // LIST_POLICIES still backs the other policy surface; "ListPoliciesPage"
    // is this table's own walk, which is a different root field and would
    // otherwise keep showing the deleted row.
    refetchQueries: [{ query: LIST_POLICIES }, "ListPoliciesPage"],
    awaitRefetchQueries: true,
  });

  const [createMutation, { loading: creating }] = useMutation<{
    createPolicy: MutationResult<AstroliftPolicy>;
  }>(CREATE_POLICY, {
    refetchQueries: [{ query: LIST_POLICIES }],
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

  /** True when the policy was created, so the sheet can close. */
  async function createPolicy(input: CreatePolicyInput): Promise<boolean> {
    const { data } = await createMutation({ variables: { input } });
    if (data?.createPolicy.ok) {
      toast.success(`Policy ${input.slug} created`);
      return true;
    }
    toast.error(data?.createPolicy.errors?.[0]?.message ?? "Create failed");
    return false;
  }

  return { table, deleting, deletePolicy, creating, createPolicy };
}
