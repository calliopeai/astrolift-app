"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_AGENT_ENVIRONMENT_SPECS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentEnvironmentSpec } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

interface EnvSpecsResp {
  agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
}

/**
 * Resolve the agent's environment spec for the settings "Model access" card —
 * the spec slug matches the agent slug (agent `foo` ↔ spec `foo`).
 */
export function useAgentModelAccess(agentSlug: string) {
  // Reactive org id (matches useAgent): the cookie read races the post-render
  // effect that sets it, so gate the query on a resolved org.
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const orgId = org?.id ?? "";
  const { data, loading, error, refetch } = useQuery<EnvSpecsResp>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });

  const spec = data?.agentEnvironmentSpecs?.find((s) => s.slug === agentSlug) ?? null;

  return {
    spec,
    loading: loading || orgLoading,
    orgId,
    error: error ? { message: error.message } : orgError ? { message: orgError.message } : null,
    onRetry: orgId ? () => void refetch() : undefined,
  };
}
