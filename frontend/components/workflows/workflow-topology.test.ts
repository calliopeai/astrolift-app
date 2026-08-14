import { describe, expect, it } from "vitest";

import type {
  WorkflowDefinitionSummary,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";

import { buildProjectWorkflowDag } from "./workflow-topology";

function stage(
  guid: string,
  order: number,
  role: string,
  options: { agent?: string; workflow?: string } = {}
): WorkflowTopologyStage {
  return {
    guid,
    order,
    kind: options.workflow ? "workflow" : "agent",
    role,
    agentRef: options.agent ?? "",
    workflowRef: options.workflow ?? "",
    agentGuid: null,
    agentName: "",
    agentSlug: options.agent ?? "",
    environmentSpecSlug: options.agent ?? "",
    resolvedModel: "",
    hasPrompt: false,
    outputKey: "",
    skillRefs: [],
    fanOutCount: null,
    fanOutDynamic: false,
    onFailure: "fail",
    timeoutSeconds: 300,
  };
}

function workflow(
  guid: string,
  slug: string,
  name: string,
  stages: WorkflowTopologyStage[]
): WorkflowDefinitionSummary {
  return {
    guid,
    slug,
    name,
    stages,
    stageCount: stages.length,
    description: "",
    patternKind: "chained",
    isEnabled: true,
    isGlobal: false,
    organizationGuid: "org-guid",
    projectGuid: "project-guid",
    projectSlug: "emr-bug-triage",
    projectTeamSlug: "engineering",
    sourceRepo: "steadymd/smd-agents",
    sourcePath: "workflows/example.toml",
    sourceRef: "main",
    createdAt: "2026-08-13T00:00:00Z",
  };
}

describe("project workflow topology hierarchy", () => {
  it("cascades referenced subflows through the parent execution path", () => {
    const intake = workflow("wf-intake", "emr-triage", "EMR bug-report triage", [
      stage("intake", 0, "intake", { agent: "emr-triage-intake" }),
      stage("decide", 1, "decide", { agent: "emr-triage-decide" }),
    ]);
    const patch = workflow("wf-patch", "emr-triage-patch", "EMR patch review", [
      stage("research", 0, "research", { agent: "emr-triage-research" }),
      stage("review", 1, "review", { agent: "emr-triage-review" }),
    ]);
    const parent = workflow("wf-parent", "emr-triage-full", "EMR triage full path", [
      stage("triage-flow", 0, "triage", { workflow: intake.slug }),
      stage("gate", 1, "worth patching", { agent: "emr-triage-gate" }),
      stage("patch-flow", 2, "patch", { workflow: patch.slug }),
    ]);

    const rows = buildProjectWorkflowDag([intake, patch, parent]);
    const roots = rows.filter((row) => (row.needs ?? []).length === 0);
    const byName = new Map(rows.map((row) => [row.name, row]));

    expect(roots).toMatchObject([{ name: parent.name, topologyKind: "workflow", needs: [] }]);
    expect(byName.get(intake.name)).toMatchObject({
      topologyKind: "subflow",
      needs: [roots[0].id],
    });
    expect(byName.get("intake · emr-triage-intake")?.needs).toEqual([byName.get(intake.name)?.id]);
    expect(byName.get("worth patching · emr-triage-gate")?.needs).toEqual([
      byName.get("decide · emr-triage-decide")?.id,
    ]);
    expect(byName.get(patch.name)?.needs).toEqual([
      byName.get("worth patching · emr-triage-gate")?.id,
    ]);
    expect(byName.get("research · emr-triage-research")?.needs).toEqual([
      byName.get(patch.name)?.id,
    ]);
  });

  it("keeps unrelated workflows as independent roots", () => {
    const first = workflow("wf-a", "a", "Workflow A", []);
    const second = workflow("wf-b", "b", "Workflow B", []);

    expect(buildProjectWorkflowDag([first, second])).toMatchObject([
      { name: "Workflow A", topologyKind: "workflow", needs: [] },
      { name: "Workflow B", topologyKind: "workflow", needs: [] },
    ]);
  });

  it("renders cyclic references without recursing forever", () => {
    const first = workflow("wf-a", "a", "Workflow A", [
      stage("to-b", 0, "nested", { workflow: "b" }),
    ]);
    const second = workflow("wf-b", "b", "Workflow B", [
      stage("to-a", 0, "nested", { workflow: "a" }),
    ]);

    const rows = buildProjectWorkflowDag([first, second]);
    expect(rows).toHaveLength(6);
    expect(rows.filter((row) => row.name === "Workflow · a")).toHaveLength(1);
    expect(rows.filter((row) => row.name === "Workflow · b")).toHaveLength(1);
  });
});
