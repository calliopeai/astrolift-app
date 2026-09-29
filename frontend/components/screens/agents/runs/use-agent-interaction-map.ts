"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { AGENT_TASK_INTERACTIONS } from "@/graphql/agents/agents.queries";
import type { AgentTaskInteractionsData } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

// 4s sits inside the LiveFlowMap 3–5s window (#1090), matching the workflow-run
// DAG (P1) and fleet map (P2). The poll is gated on task terminality below.
const POLL_MS = 4000;

// Terminal AgentTask.Status values (backend contract, mirrors use-agent-run-detail
// / agents-client): a successful task is "completed", not "succeeded". A terminal
// task stops the poll and renders a static map (no pulses).
const TERMINAL = new Set(["completed", "failed", "timed_out", "cancelled"]);

/**
 * Per-agent-task interactions (#1092 — LiveFlowMap P3). Mirrors P1's poll
 * shape: `agentTaskInteractions` is refetched every {@link POLL_MS} while the
 * run is non-terminal (full-set, since=null). Once the task is terminal the
 * poll stops. `orgId` is resolved from the active org (the run-detail route
 * carries only the task id); the query stays skipped until it lands.
 */
export function useAgentInteractionMap(taskId: string, taskStatus: string) {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? null;
  const isTerminal = TERMINAL.has(taskStatus);

  const { data, loading, error } = useQuery<AgentTaskInteractionsData>(AGENT_TASK_INTERACTIONS, {
    variables: { orgId: orgId ?? "", taskId, since: null, limit: 200 },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
    pollInterval: isTerminal ? 0 : POLL_MS,
  });

  const interactions = React.useMemo(() => data?.agentTaskInteractions ?? [], [data]);

  return {
    taskStatus,
    isTerminal,
    interactions,
    loading: !orgId || loading,
    error: error ? error.message : null,
  };
}

export type AgentInteractionMapState = ReturnType<typeof useAgentInteractionMap>;
