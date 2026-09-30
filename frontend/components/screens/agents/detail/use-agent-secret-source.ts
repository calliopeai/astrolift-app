"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";
import type {
  AgentSecretSourceDetailQuery,
  AgentSecretSourceDetailQueryVariables,
  AgentSecretSourceOptionsQuery,
  AgentSecretSourceOptionsQueryVariables,
} from "@/graphql/__generated__/operations";
import {
  AGENT_SECRET_SOURCE_DETAIL,
  AGENT_SECRET_SOURCE_OPTIONS,
} from "@/graphql/agents/agent-secret-source.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useDebounce } from "@/hooks/use-debounce";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import type { AgentSecretSourceProps, SecretSourceOption } from "./AgentSecretSource";

const PAGE_SIZE = 20;

/** Explicit recipe selection, independently checked at its current active-org scope. */
export function useAgentSecretSource(agentSlug: string) {
  const { org, error: orgError } = useActiveOrg();
  const perms = useMyPermissions();
  const orgId = org?.id ?? "";
  const scope = JSON.stringify([orgId, agentSlug]);
  const initial = { scope, search: "", page: 1, selection: null as SecretSourceOption | null };
  const [stored, setStored] = React.useState(initial);
  // Derive from the current scope before issuing any query, even when a caller
  // keeps this hook mounted while the org or agent route changes.
  const state = stored.scope === scope ? stored : initial;
  if (stored.scope !== scope) setStored(initial);
  const search = useDebounce(state.search);
  const access: AgentSecretSourceProps["access"] =
    orgError || perms.error
      ? "error"
      : !orgId || perms.loading
        ? "loading"
        : perms.can("agent_env_spec.read")
          ? "ready"
          : "denied";
  const readReady = access === "ready";
  const options = useQuery<AgentSecretSourceOptionsQuery, AgentSecretSourceOptionsQueryVariables>(
    AGENT_SECRET_SOURCE_OPTIONS,
    {
      variables: { orgId, search: search.trim() || null, page: state.page, pageSize: PAGE_SIZE },
      skip: !readReady,
      fetchPolicy: "cache-and-network",
      context: { headers: { "X-Astrolift-Organization": orgId } },
    }
  );
  const detail = useQuery<AgentSecretSourceDetailQuery, AgentSecretSourceDetailQueryVariables>(
    AGENT_SECRET_SOURCE_DETAIL,
    {
      variables: { orgId, slug: state.selection?.slug ?? "" },
      skip: !readReady || !state.selection,
      fetchPolicy: "network-only",
      context: { headers: { "X-Astrolift-Organization": orgId } },
    }
  );
  const page = readReady ? options.data?.agentEnvironmentSpecsPage : undefined;
  const choices = page?.items ?? [];
  const optionsLoading = options.loading || state.search !== search;
  const spec =
    readReady && !detail.loading && !detail.error ? detail.data?.agentEnvironmentSpec : null;
  // The recorded choice is an identity, not just a reusable slug: deletion and
  // recreation of that slug must not silently target another secret packet.
  const confirmed =
    state.selection && spec?.id === state.selection.id && spec.slug === state.selection.slug
      ? spec
      : null;
  const sourceState: AgentSecretSourceProps["sourceState"] = !state.selection
    ? "unselected"
    : !readReady || detail.loading
      ? "loading"
      : detail.error
        ? "error"
        : confirmed
          ? "confirmed"
          : "unavailable";
  const source: Omit<AgentSecretSourceProps, "children"> = {
    access,
    options: choices,
    optionsLoading,
    optionsError: options.error?.message ?? null,
    search: state.search,
    onSearch: (value) => setStored((current) => ({ ...current, search: value, page: 1 })),
    page: page?.page ?? state.page,
    totalCount: page?.totalCount ?? 0,
    pageSize: PAGE_SIZE,
    onPage: (value) => {
      if (!optionsLoading && value >= 1) setStored((current) => ({ ...current, page: value }));
    },
    onRetryOptions: () => {
      if (readReady) void options.refetch().catch(() => undefined);
    },
    selection: state.selection,
    onSelect: (id) => {
      const choice =
        !optionsLoading && !options.error ? choices.find((row) => row.id === id) : null;
      if (readReady && choice) setStored((current) => ({ ...current, selection: choice }));
    },
    onClear: () => setStored((current) => ({ ...current, selection: null })),
    sourceState,
    onVerify: () => {
      if (readReady && state.selection) void detail.refetch().catch(() => undefined);
    },
    canListSecrets: !perms.loading && !perms.error && perms.can("secret.list"),
  };
  return {
    source,
    confirmedSpec: sourceState === "confirmed" ? confirmed : null,
    editorKey: JSON.stringify([scope, confirmed?.id]),
    canReadSecretBundles:
      !perms.loading && !perms.error && perms.can("secret.list") && perms.can("secret.read"),
  };
}
