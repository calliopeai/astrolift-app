"use client";

import { useQuery } from "@apollo/client/react";
import { useState } from "react";

import { useListState } from "@/components/list/use-list-state";
import {
  AGENT_ENVIRONMENT_SPEC_DETAIL,
  AGENT_ENVIRONMENT_SPECS_PAGE,
} from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import {
  type EnvironmentSpec,
  ENVIRONMENT_SPECS_LIST,
  environmentSpecsVariables,
} from "./environment-specs";

type PageData = {
  agentEnvironmentSpecsPage: {
    items: EnvironmentSpec[];
    totalCount: number;
    page: number;
    pageSize: number;
  };
};

export const useEnvironmentSpecs = () => {
  const list = useListState(ENVIRONMENT_SPECS_LIST);
  const { org, loading: orgLoading } = useActiveOrg();
  const orgId = org?.id ?? "";
  const { data, loading, error, refetch } = useQuery<PageData>(AGENT_ENVIRONMENT_SPECS_PAGE, {
    variables: { orgId, ...environmentSpecsVariables({ ...list.state, filters: list.filters }) },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });
  const [snapshot, setSnapshot] = useState<{ orgId: string; data: PageData } | null>(null);
  if (data && (snapshot?.orgId !== orgId || snapshot.data !== data)) setSnapshot({ orgId, data });
  const shown = data ?? (snapshot?.orgId === orgId ? snapshot.data : undefined);
  const page = orgId ? shown?.agentEnvironmentSpecsPage : undefined;
  return {
    list,
    rows: page?.items ?? [],
    totalCount: page?.totalCount ?? 0,
    loading: (loading || orgLoading || !orgId) && !page && !error,
    stale: loading && Boolean(page),
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch().catch(() => undefined);
    },
  };
};

export const useEnvironmentSpec = (slug: string) => {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const { data, loading, error, refetch } = useQuery<{
    agentEnvironmentSpec: EnvironmentSpec | null;
  }>(AGENT_ENVIRONMENT_SPEC_DETAIL, {
    variables: { slug, orgId },
    fetchPolicy: "cache-and-network",
    skip: !slug || !orgId,
    context: { headers: { "X-Astrolift-Organization": orgId } },
  });
  const key = `${orgId}:${slug}`;
  const [snapshot, setSnapshot] = useState<{ key: string; spec: EnvironmentSpec | null } | null>(
    null
  );
  if (data && (snapshot?.key !== key || snapshot.spec !== data.agentEnvironmentSpec)) {
    setSnapshot({ key, spec: data.agentEnvironmentSpec });
  }
  const spec = data ? data.agentEnvironmentSpec : snapshot?.key === key ? snapshot.spec : null;
  return {
    spec: orgId ? spec : null,
    loading: (loading || !orgId) && !data && snapshot?.key !== key && !error,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch().catch(() => undefined);
    },
  };
};
