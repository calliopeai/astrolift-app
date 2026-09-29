"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useListState } from "@/components/list/use-list-state";
import { GET_AGENT_DETAIL } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentDetail } from "@/graphql/agents/agents.types";

import {
  AGENT_SKILLS_LIST,
  agentToolsList,
  selectSkills,
  selectTools,
  toolAdapters,
  toolRows,
} from "./agent-skills-list";

interface AgentDetailResp {
  agent: AstroliftAgentDetail | null;
}

/**
 * The `agent(orgId, slug)` join both lists read: the same query and
 * variables as the Build section, so Apollo serves it once.
 */
function useAgentDetail(slug: string, orgId: string) {
  const q = useQuery<AgentDetailResp>(GET_AGENT_DETAIL, {
    variables: { orgId, slug },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  return {
    skills: q.data?.agent?.skills ?? [],
    loading: q.loading && !q.data,
    error: q.error && !q.data ? { message: q.error.message } : null,
    onRetry: () => void q.refetch(),
  };
}

/** Skills & tools › Skills: the agent's bound skills, URL-state list. */
export function useAgentSkills(slug: string, orgId: string) {
  const detail = useAgentDetail(slug, orgId);
  const list = useListState(AGENT_SKILLS_LIST);
  const { state } = list;
  const { rows, totalCount } = React.useMemo(
    () =>
      selectSkills(detail.skills, list.filters, state.q, state.sort, state.page, state.pageSize),
    [detail.skills, list.filters, state.q, state.sort, state.page, state.pageSize]
  );
  return { ...detail, list, rows, totalCount };
}

/** Skills & tools › Tools: the tools the agent's skills carry, URL-state list. */
export function useAgentTools(slug: string, orgId: string) {
  const detail = useAgentDetail(slug, orgId);
  const all = React.useMemo(() => toolRows(detail.skills), [detail.skills]);
  const adapters = toolAdapters(all).join(",");
  const def = React.useMemo(() => agentToolsList(adapters ? adapters.split(",") : []), [adapters]);
  const list = useListState(def);
  const { state } = list;
  const { rows, totalCount } = React.useMemo(
    () => selectTools(all, list.filters, state.q, state.sort, state.page, state.pageSize),
    [all, list.filters, state.q, state.sort, state.page, state.pageSize]
  );
  return { ...detail, list, rows, totalCount };
}
