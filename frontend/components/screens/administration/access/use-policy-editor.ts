"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import type { ConditionCatalogEntry } from "@/components/access/PolicyConditionHelp";
import type { PolicySimulation } from "@/components/access/PolicySimulationPanel";
import {
  GET_POLICY,
  POLICY_CONDITION_CATALOG,
  POLICY_SIMULATION,
} from "@/graphql/access/access.queries";
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

interface PolicyResp {
  astroliftPolicy: AstroliftPolicy | null;
}

interface CatalogResp {
  astroliftPolicyConditionCatalog: { conditions: ConditionCatalogEntry[] };
}

interface SimulationResp {
  astroliftPolicySimulation: PolicySimulation;
}

/** How far back the review replays recorded decisions, and how many rows it lists. */
const SIMULATION_DAYS = 7;
const SIMULATION_LIMIT = 50;

/** Both saves refresh the list's own page by name, this policy, and the flat list. */
const REFETCH = [{ query: LIST_POLICIES }, "ListPoliciesPage", "GetPolicy"];

/**
 * The data half of PolicyEditorScreen, for New (no `id`) and for one policy
 * (`astroliftPolicy`, skipped on New). The server's condition catalog says
 * what each condition needs, and the review runs the draft through
 * `astroliftPolicySimulation` before anything is written. An edit sends
 * `ifMatchVersion`, so a policy someone else saved since it was opened is
 * refused rather than overwritten, and the refusal shows in place.
 */
export function usePolicyEditor(id?: string) {
  const router = useRouter();
  const client = useApolloClient();
  const perms = useMyPermissions();
  const existing = useQuery<PolicyResp>(GET_POLICY, {
    variables: { id: id ?? "" },
    skip: !id,
    fetchPolicy: "cache-and-network",
  });
  const policy = id ? (existing.data?.astroliftPolicy ?? null) : null;
  const catalog = useQuery<CatalogResp>(POLICY_CONDITION_CATALOG, { fetchPolicy: "cache-first" });

  /** The draft against today's holders and the last week of decisions. Throws on a transport error. */
  async function simulate(input: PolicyInput): Promise<PolicySimulation> {
    const { data, error } = await client.query<SimulationResp>({
      query: POLICY_SIMULATION,
      variables: {
        draft: {
          effect: input.effect,
          actionPattern: input.actionPattern,
          scopeLevel: input.scopeLevel,
          resourcePattern: input.resourcePattern,
          conditions: input.conditions,
          actorPattern: input.actorPattern,
        },
        days: SIMULATION_DAYS,
        limit: SIMULATION_LIMIT,
      },
      fetchPolicy: "network-only",
    });
    if (!data?.astroliftPolicySimulation) throw error ?? new Error("The simulation failed");
    return data.astroliftPolicySimulation;
  }

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
    catalog: {
      conditions: catalog.data?.astroliftPolicyConditionCatalog.conditions ?? [],
      loading: catalog.loading && !catalog.data,
      error: catalog.error ? { message: catalog.error.message } : null,
    },
    simulate,
    saving: creating || updating,
    onSave,
    onCancel: () => router.push(POLICIES_HREF),
  };
}
