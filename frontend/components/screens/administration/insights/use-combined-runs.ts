"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { exportCsv } from "@/components/list/exportCsv";
import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  LIST_DEPLOYMENTS_PAGE,
  LIST_SCHEDULED_JOB_RUNS_PAGE,
} from "@/graphql/lifecycle/lifecycle.queries";
import { useModules } from "@/graphql/user/user.hooks";
import { LIST_WORKFLOW_DEFINITION_RUNS } from "@/graphql/workflows/tiered.queries";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";

import {
  type AgentTaskSource,
  type DeploymentSource,
  filterRuns,
  fromAgentTask,
  fromDeployment,
  fromJobRun,
  fromWorkflowRun,
  type JobRunSource,
  mergeRuns,
  pageRuns,
  RUN_AUDIT_LIST,
  RUN_CSV,
  type RunKind,
} from "./combined-runs";
import type { CombinedRunsScreenProps } from "./CombinedRunsScreen";

/**
 * INTERIM, CLIENT-SIDE MERGE. There is no combined run query yet, so this
 * reads the newest page of each of the four existing queries, maps them to
 * one row shape, and merges, filters, sorts and pages them in the browser.
 * The list is therefore the newest SOURCE_LIMIT of each kind, not the whole
 * history; the screen says so under the table. Replace the four queries
 * with the one paged `astroliftRunAudit(filter, search, sort, first, after)`
 * query (see the report's needsBackend) and drop the merge.
 */
const SOURCE_LIMIT = 100;
const POLL_MS = 15_000;

interface AgentTasksResp {
  agentTasks: AgentTaskSource[];
}
interface WorkflowRunsResp {
  workflowDefinitionRuns: WorkflowDefinitionRun[];
}
interface DeploymentsResp {
  astroliftDeploymentsPage: { items: DeploymentSource[]; nextCursor: string | null };
}
interface JobRunsResp {
  astroliftScheduledJobRunsPage: { items: JobRunSource[]; nextCursor: string | null };
}

const SOURCE_LABEL: Record<RunKind, string> = {
  agent: "agent runs",
  workflow: "workflow runs",
  deployment: "deployments",
  job: "job runs",
};

/** The run audit's data half: four source queries, merged (see above). */
export function useCombinedRuns(): CombinedRunsScreenProps {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const modules = useModules();
  const list = useListState(RUN_AUDIT_LIST);
  const [now] = React.useState(() => Date.now());

  // A source shows when the viewer may see its module and the kind chip,
  // if any, asks for it; a source the chip rules out is not fetched.
  const kind = list.filters.kind as RunKind | undefined;
  const wants = (k: RunKind) => !kind || kind === k;
  const on = {
    agent: wants("agent") && modules.canView("agents"),
    workflow: wants("workflow") && modules.canView("workflows"),
    deployment: wants("deployment") && modules.canView("apps"),
    job: wants("job") && modules.canView("apps"),
  };
  const firstPage = list.state.after === null;
  const common = {
    fetchPolicy: "cache-and-network" as const,
    pollInterval: firstPage ? POLL_MS : 0,
  };

  const agent = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    ...common,
    variables: { orgId, status: null, workloadId: null },
    skip: !orgId || modules.loading || !on.agent,
  });
  const workflow = useQuery<WorkflowRunsResp>(LIST_WORKFLOW_DEFINITION_RUNS, {
    ...common,
    variables: { orgId: orgId || null, projectId: null, status: null, limit: SOURCE_LIMIT },
    skip: !orgId || modules.loading || !on.workflow,
  });
  const deployment = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS_PAGE, {
    ...common,
    variables: { limit: SOURCE_LIMIT, after: null },
    skip: modules.loading || !on.deployment,
  });
  const job = useQuery<JobRunsResp>(LIST_SCHEDULED_JOB_RUNS_PAGE, {
    ...common,
    variables: { limit: SOURCE_LIMIT, after: null },
    skip: modules.loading || !on.job,
  });

  const sources = { agent, workflow, deployment, job };
  const active = (Object.keys(sources) as RunKind[]).filter((k) => on[k]);

  // The React Compiler memoises these; the merge is a few hundred rows.
  const merged = mergeRuns(
    on.agent ? (agent.data?.agentTasks ?? []).map(fromAgentTask) : [],
    on.workflow ? (workflow.data?.workflowDefinitionRuns ?? []).map(fromWorkflowRun) : [],
    on.deployment
      ? (deployment.data?.astroliftDeploymentsPage.items ?? []).map(fromDeployment)
      : [],
    on.job ? (job.data?.astroliftScheduledJobRunsPage.items ?? []).map(fromJobRun) : []
  );
  const filtered = filterRuns(merged, list.filters, list.state.q, now);
  const page = pageRuns(filtered, list.state.after, list.state.pageSize);

  const failed = active.filter((k) => sources[k].error && !sources[k].data);
  const pending = active.some((k) => sources[k].loading && !sources[k].data);
  const hasAny = active.some((k) => sources[k].data);

  const held = useHeldRows(page.rows, (r) => r.key, {
    live: firstPage && !pending,
    resetKey: JSON.stringify(list.filters) + list.state.q + list.state.pageSize,
  });

  // A source whose first page came back full may hold more; the count is then a floor.
  const capped =
    (on.agent && (agent.data?.agentTasks.length ?? 0) >= SOURCE_LIMIT) ||
    (on.workflow && (workflow.data?.workflowDefinitionRuns.length ?? 0) >= SOURCE_LIMIT) ||
    (on.deployment && Boolean(deployment.data?.astroliftDeploymentsPage.nextCursor)) ||
    (on.job && Boolean(job.data?.astroliftScheduledJobRunsPage.nextCursor));

  const refetchAll = () => {
    for (const k of active) void sources[k].refetch();
  };

  const allFailed = active.length > 0 && failed.length === active.length;

  return {
    list,
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: (modules.loading || !orgId || pending) && !hasAny,
    stale: pending && hasAny,
    error: allFailed
      ? { message: sources[failed[0]].error?.message ?? "The run sources failed to load." }
      : null,
    onRetry: refetchAll,
    nextCursor: page.nextCursor,
    totalCount: hasAny ? filtered.length : null,
    approximateCount: capped,
    unavailable: failed.map((k) => SOURCE_LABEL[k]),
    coverage: capped
      ? `Merged from the newest ${SOURCE_LIMIT} of each kind; older runs are on each kind's own list.`
      : null,
    onExportCsv: () =>
      exportCsv(`run-audit-${new Date().toISOString().slice(0, 10)}`, filtered, RUN_CSV),
  };
}
