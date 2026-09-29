"use client";

import * as React from "react";

import { useListState } from "@/components/list/use-list-state";
import { useAgentSecrets } from "@/components/screens/agents/list/use-agent-secrets";

import { agentSecretsList, secretProviders, selectSecrets } from "./agent-secrets-list";

/**
 * Secrets › Values: the env spec's secret refs (the spec slug is the agent
 * slug) and their mutations, from the same hook the dispatch dialog uses,
 * on URL list state with the page answered here. The data half of
 * AgentSecretValues.
 */
export function useAgentSecretValues(slug: string) {
  const secrets = useAgentSecrets(slug, true);
  const providers = secretProviders(secrets.rows).join("\n");
  const def = React.useMemo(
    () => agentSecretsList(providers ? providers.split("\n") : []),
    [providers]
  );
  const list = useListState(def);
  const { state } = list;
  const { rows, totalCount } = React.useMemo(
    () =>
      selectSecrets(secrets.rows, list.filters, state.q, state.sort, state.page, state.pageSize),
    [secrets.rows, list.filters, state.q, state.sort, state.page, state.pageSize]
  );
  return {
    ...secrets,
    list,
    rows,
    totalCount,
    loading: secrets.loading && secrets.rows.length === 0,
  };
}

export type AgentSecretValuesState = ReturnType<typeof useAgentSecretValues>;
