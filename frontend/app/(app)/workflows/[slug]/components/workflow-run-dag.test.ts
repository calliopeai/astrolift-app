import { describe, expect, it } from "vitest";

import type { WorkflowStage, WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

import { buildRunDagStages } from "./workflow-run-dag";

const nestedStage: WorkflowStage = {
  guid: "stage-guid",
  order: 0,
  kind: "workflow",
  role: "",
  prompt: "",
  approvers: [],
  agentRef: "",
  workflowRef: "inner-triage",
  environmentSpecSlug: "",
  outputKey: "inner_result",
  skillRefs: [],
  fanOutCount: null,
  onFailure: "fail",
  timeoutSeconds: 300,
  createdAt: "2026-08-13T00:00:00Z",
  agentDefinitionGuid: null,
  agentDefinitionName: null,
};

describe("nested workflow run graph", () => {
  it("renders the child as a navigable workflow node before it starts", () => {
    expect(buildRunDagStages([nestedStage], [])).toMatchObject([
      {
        name: "Workflow · inner-triage",
        status: "pending",
        href: "/workflows/inner-triage/observe",
      },
    ]);
  });

  it("prefers the linked execution's resolved child slug", () => {
    const execution: WorkflowStageExecution = {
      guid: "execution-guid",
      status: "running",
      attemptNumber: 1,
      startedAt: "2026-08-13T00:00:00Z",
      endedAt: null,
      output: null,
      failure: null,
      errorMessage: "",
      createdAt: "2026-08-13T00:00:00Z",
      executionId: "42",
      stageGuid: nestedStage.guid,
      stageKind: "workflow",
      stageOrder: 0,
      agentRunGuid: null,
      childWorkflowRunGuid: "child-run-guid",
      childWorkflowDefinitionSlug: "resolved-inner",
      childWorkflowStatus: "running",
    };
    expect(buildRunDagStages([nestedStage], [execution])[0]).toMatchObject({
      status: "running",
      href: "/workflows/resolved-inner/observe?run=child-run-guid",
    });
  });
});
