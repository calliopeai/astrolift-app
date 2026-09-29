import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";

import { LONG_TASK_RUNS, TASK_RUNS } from "@/components/screens/jobs/jobs-tasks.fixtures";
import {
  COMPLETED_TASK,
  FAILED_TASK,
  RUNNING_TASK,
} from "@/components/screens/agents/runs/agent-runs.fixtures";

import { fromAgentTask, fromTaskRun, fromWorkflowRun, type RunRow, sortRunRows } from "./runs-list";
import type { RunsScreenProps } from "./RunsScreen";

const ago = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString();

export function workflowRun(
  guid: string,
  patch: Partial<WorkflowDefinitionRun> = {}
): WorkflowDefinitionRun {
  return {
    guid,
    definitionGuid: "def-1",
    definitionSlug: "nightly-sync",
    definitionName: "Nightly sync",
    projectGuid: "proj-1",
    projectSlug: "sales",
    status: "completed",
    temporalWorkflowId: `wf-${guid}`,
    temporalRunId: `run-${guid}`,
    currentStageOrder: 3,
    currentStageRole: "publish",
    parentRunGuid: null,
    parentStageExecutionGuid: null,
    nestingDepth: 0,
    childRunCount: 0,
    startedAt: ago(3600),
    endedAt: ago(3300),
    ...patch,
  };
}

export const AGENT_RUN_ROWS: RunRow[] = [
  fromAgentTask(RUNNING_TASK),
  fromAgentTask({ ...COMPLETED_TASK, id: "6e11b0c2-3a4f-4e5d-8c9b-1a2b3c4d5e6f" }),
  fromAgentTask({
    ...FAILED_TASK,
    id: "5d00a9b1-293e-4d4c-9b8a-0f1e2d3c4b5a",
    startedAt: ago(900),
  }),
  fromAgentTask({
    ...RUNNING_TASK,
    id: "4cff98a0-182d-4c3b-8a79-fe0d1c2b3a49",
    status: "queued",
    startedAt: null,
    createdAt: ago(20),
    vncEnabled: false,
    vncUrl: "",
  }),
];

export const RUN_ROWS: RunRow[] = sortRunRows(
  [
    ...AGENT_RUN_ROWS,
    fromWorkflowRun(workflowRun("wf-0001")),
    fromWorkflowRun(
      workflowRun("wf-0002", { status: "running", startedAt: ago(120), endedAt: null })
    ),
    fromWorkflowRun(
      workflowRun("wf-0003", {
        status: "failed",
        parentRunGuid: "wf-0001",
        definitionSlug: "enrich-accounts",
        definitionName: "Enrich accounts",
      })
    ),
    ...TASK_RUNS.map((r) => fromTaskRun(r, "leo")),
  ],
  []
);

const LONG = "a-very-long-identifier-that-keeps-going-to-show-how-the-layout-wraps".repeat(3);

export const LONG_RUN_ROWS: RunRow[] = [
  fromAgentTask({
    ...RUNNING_TASK,
    id: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    agentSlug: LONG,
    projectSlug: `project-${LONG}`,
  }),
  fromWorkflowRun(
    workflowRun("arn:aws:states:us-east-2:316992255370:execution:" + LONG, {
      definitionName: `https://hooks.example.com/${LONG}`,
      projectSlug: LONG,
    })
  ),
  ...LONG_TASK_RUNS.map((r) => fromTaskRun(r, null)),
];

const noop = () => {};

export const RUNS_SCREEN: Omit<RunsScreenProps, "list"> = {
  rows: RUN_ROWS,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  nextCursor: "o:25",
  totalCount: 42,
  approximateCount: false,
  unavailable: [],
  coverage: null,
  lookup: (key) => [...RUN_ROWS, ...LONG_RUN_ROWS].find((r) => r.key === key) ?? null,
  onCancel: async () => {},
};
