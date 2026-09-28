"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { CREATE_POLICY } from "@/graphql/identity/identity.mutations";
import { LIST_POLICIES } from "@/graphql/identity/identity.queries";
import type {
  AstroliftPolicy,
  MutationResult,
  PolicyEffect,
  ScopeKind,
} from "@/graphql/identity/identity.types";

export interface CreatePolicyInput {
  name: string;
  slug: string;
  scopeLevel: ScopeKind;
  effect: PolicyEffect;
  actionPattern: string;
  conditions: unknown;
}

export const POLICIES_HREF = "/administration/policies";

/**
 * The data half of CreatePolicyScreen: the create mutation, and the way back
 * to the list. The list refetches its own page on mount (cache-and-network),
 * so the new policy is at the top when it lands.
 */
export function useCreatePolicy() {
  const router = useRouter();
  const [createMutation, { loading: creating }] = useMutation<{
    createPolicy: MutationResult<AstroliftPolicy>;
  }>(CREATE_POLICY, {
    refetchQueries: [{ query: LIST_POLICIES }],
    awaitRefetchQueries: true,
  });

  /**
   * Resolves null when the policy was created (and navigates to the list),
   * or the server's reason, which the review step shows in place.
   */
  async function createPolicy(input: CreatePolicyInput): Promise<string | null> {
    const { data } = await createMutation({ variables: { input } });
    if (data?.createPolicy.ok) {
      toast.success(`Policy ${input.slug} created`);
      router.push(POLICIES_HREF);
      return null;
    }
    return data?.createPolicy.errors?.[0]?.message ?? "Create failed";
  }

  return {
    creating,
    createPolicy,
    onCancel: () => router.push(POLICIES_HREF),
  };
}
