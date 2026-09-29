"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import {
  LIST_AGENT_ENVIRONMENT_SPECS,
  LIST_AGENT_FLEET,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentTask,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

type FleetData = { agentFleet: AstroliftAgentListItem[] };
type TaskData = { agentTasks: AstroliftAgentTask[] };
type RuntimeData = { agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[] };

export const ACTIVE_TASK_STATUSES = new Set(["queued", "provisioning", "running"]);
export const FAILING_TASK_STATUSES = new Set(["failed", "timed_out"]);

/**
 * The data half of FleetOverviewScreen: one fleet poll, one task poll and
 * the runtimes. The stat tiles and the three summaries all read these same
 * three queries (list rule 2: two panels never fetch the same thing twice);
 * the full lists live on their own routes and fetch there.
 */
export function useFleetOverview() {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const fleet = useQuery<FleetData>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
    pollInterval: 10000,
  });
  const tasks = useQuery<TaskData>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null, workloadId: null },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });
  const runtimes = useQuery<RuntimeData>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const agents = fleet.data?.agentFleet ?? [];
  const rows = React.useMemo(
    () =>
      [...(tasks.data?.agentTasks ?? [])].sort(
        (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)
      ),
    [tasks.data?.agentTasks]
  );

  return {
    agentCount: agents.length,
    runningAgentCount: agents.filter((agent) => agent.runningCount > 0).length,
    runtimeCount: runtimes.data?.agentEnvironmentSpecs.length,
    activeTaskCount: rows.filter((task) => ACTIVE_TASK_STATUSES.has(task.status)).length,
    incidentCount: rows.filter((task) => FAILING_TASK_STATUSES.has(task.status)).length,
    loading: fleet.loading || tasks.loading || runtimes.loading,
    runtimes: runtimes.data?.agentEnvironmentSpecs ?? [],
    runtimesLoading: runtimes.loading,
    refresh: () => {
      void Promise.all([fleet.refetch(), tasks.refetch(), runtimes.refetch()]);
    },
    /** Newest first; the summary shows the top rows. */
    recentTasks: rows,
    tasksLoading: tasks.loading && !tasks.data,
    tasksError: tasks.error && !tasks.data ? tasks.error.message : null,
    agents,
    agentsLoading: fleet.loading && !fleet.data,
    agentsError: fleet.error && !fleet.data ? fleet.error.message : null,
    runtimesError: runtimes.error && !runtimes.data ? runtimes.error.message : null,
  };
}
