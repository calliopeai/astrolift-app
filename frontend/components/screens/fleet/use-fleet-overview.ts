"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useCursorTable } from "@/components/data-table";
import {
  LIST_AGENT_ENVIRONMENT_SPECS,
  LIST_AGENT_FLEET,
  LIST_AGENT_FLEET_PAGE,
  LIST_AGENT_TASKS,
  LIST_AGENT_TASKS_PAGE,
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
type FleetPageData = {
  agentFleetPage: {
    items: AstroliftAgentListItem[];
    nextCursor: string | null;
    totalCount: number | null;
  };
};
type TaskPageData = {
  agentTasksPage: {
    items: AstroliftAgentTask[];
    nextCursor: string | null;
    totalCount: number | null;
  };
};

export const ACTIVE_TASK_STATUSES = new Set(["queued", "provisioning", "running"]);
export const FAILING_TASK_STATUSES = new Set(["failed", "timed_out"]);

/**
 * The data half of FleetOverviewScreen: fleet, task and runtime polls, the
 * two paginated tables, and the counts the stat tiles show.
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
  const agentTable = useCursorTable<AstroliftAgentListItem>({
    query: LIST_AGENT_FLEET_PAGE,
    variables: { orgId },
    extract: (data) => (data as FleetPageData | undefined)?.agentFleetPage,
    searchVariable: "search",
    urlKey: "fleet-agent",
    skip: !orgId,
  });
  const taskTable = useCursorTable<AstroliftAgentTask>({
    query: LIST_AGENT_TASKS_PAGE,
    variables: { orgId, status: null, workloadId: null },
    extract: (data) => (data as TaskPageData | undefined)?.agentTasksPage,
    searchVariable: "search",
    urlKey: "fleet-task",
    skip: !orgId,
    pollInterval: 10000,
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
    agentTable,
    taskTable,
  };
}
