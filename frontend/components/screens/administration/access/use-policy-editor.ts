"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { CREATE_POLICY, UPDATE_POLICY } from "@/graphql/identity/identity.mutations";
import { LIST_POLICIES } from "@/graphql/identity/identity.queries";
import type { AstroliftPolicy, MutationResult, ScopeKind } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { POLICIES_HREF } from "./policy-routes";

/** What the editor submits: the policy row's own fields, JSON halves included. */
export interface PolicyInput {
  name: string;
  slug: string;
  description: string;
  scopeLevel: ScopeKind;
  effect: "ALLOW" | "DENY";
  actionPattern: string;
  resourcePattern: Record<string, unknown>;
  conditions: unknown[];
  actorPattern: Record<string, unknown>;
}

interface PoliciesResp {
  astroliftPolicies: AstroliftPolicy[];
}

/** Both saves refresh the list's own page by name, and the flat list this page reads. */
const REFETCH = [{ query: LIST_POLICIES }, "ListPoliciesPage"];

/**
 * The data half of PolicyEditorScreen, for New (no `id`) and for one policy.
 * There is no single-policy query, so an existing policy is read from the
 * flat list (capped at 200 by the backend); the list query is skipped on New.
 * An edit sends `ifMatchVersion`, so a policy someone else saved since it was
 * opened is refused rather than overwritten, and the refusal shows in place.
 */
export function usePolicyEditor(id?: string) {
  const router = useRouter();
  const perms = useMyPermissions();
  const existing = useQuery<PoliciesResp>(LIST_POLICIES, {
    skip: !id,
    fetchPolicy: "cache-and-network",
  });
  const policy = id ? (existing.data?.astroliftPolicies.find((p) => p.id === id) ?? null) : null;

  const [create, { loading: creating }] = useMutation<{
    createPolicy: MutationResult<AstroliftPolicy>;
  }>(CREATE_POLICY, { refetchQueries: REFETCH, awaitRefetchQueries: true });
  const [update, { loading: updating }] = useMutation<{
    updatePolicy: MutationResult<AstroliftPolicy>;
  }>(UPDATE_POLICY, { refetchQueries: REFETCH, awaitRefetchQueries: true });

  /** Null when saved (and back on the list), or the server's reason. */
  async function onSave(input: PolicyInput): Promise<string | null> {
    if (policy) {
      const { slug: _slug, scopeLevel: _scope, ...fields } = input;
      const { data } = await update({
        variables: { input: { id: policy.id, ...fields, ifMatchVersion: policy.version } },
      });
      if (data?.updatePolicy.ok) {
        toast.success(`Policy ${policy.slug} saved`);
        router.push(POLICIES_HREF);
        return null;
      }
      return data?.updatePolicy.errors?.[0]?.message ?? "Save failed";
    }
    const { data } = await create({ variables: { input } });
    if (data?.createPolicy.ok) {
      toast.success(`Policy ${input.slug} created`);
      router.push(POLICIES_HREF);
      return null;
    }
    return data?.createPolicy.errors?.[0]?.message ?? "Create failed";
  }

  return {
    mode: id ? ("edit" as const) : ("create" as const),
    id: id ?? null,
    policy,
    loading: Boolean(id) && existing.loading && !existing.data,
    error: existing.error ? { message: existing.error.message } : null,
    onRetry: () => {
      void existing.refetch();
    },
    canManage: perms.can("org.update"),
    saving: creating || updating,
    onSave,
    onCancel: () => router.push(POLICIES_HREF),
  };
}
