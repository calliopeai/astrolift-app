"use client";

import { useQuery } from "@apollo/client/react";

import { useListState } from "@/components/list/use-list-state";
import { useAgentSecrets } from "@/components/screens/agents/list/use-agent-secrets";
import { AGENT_SECRET_STATUS_PAGE } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

import { AGENT_SECRETS_LIST, agentSecretsPageVariables } from "./agent-secrets-list";

interface SecretStatusPageResp {
  agentEnvironmentSpecSecretStatusPage: {
    items: AstroliftAgentSecretStatus[];
    totalCount: number;
    error: string | null;
  };
}

/**
 * Secrets › Values: one numbered page of the explicitly selected env spec's
 * secret refs from `agentEnvironmentSpecSecretStatusPage`
 * (#2155), on URL list state, plus the mutations the dispatch dialog uses.
 * The server probes the store, filters, sorts and counts. The data half of
 * AgentSecretValues.
 */
export function useAgentSecretValues(slug: string) {
  const secrets = useAgentSecrets(slug, false);
  const list = useListState(AGENT_SECRETS_LIST);
  const { state } = list;
  const query = useQuery<SecretStatusPageResp>(AGENT_SECRET_STATUS_PAGE, {
    variables: {
      slug,
      ...agentSecretsPageVariables({
        q: state.q,
        filters: list.filters,
        sort: state.sort,
        page: state.page,
        pageSize: state.pageSize,
      }),
    },
    skip: !slug,
    fetchPolicy: "cache-and-network",
  });
  const data = query.data ?? query.previousData;
  const page = data?.agentEnvironmentSpecSecretStatusPage;
  const rows = page?.items ?? [];
  return {
    ...secrets,
    list,
    rows,
    totalCount: page?.totalCount ?? rows.length,
    loading: query.loading && !data && !query.error,
    // Rows on screen answer the previous list state while the next loads.
    stale: query.loading && !query.data && Boolean(data),
    error: query.error ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
    /** Why the store could not answer at all: no agent cluster, no store, no spec. */
    readError: page?.error ?? null,
  };
}

export type AgentSecretValuesState = ReturnType<typeof useAgentSecretValues>;
