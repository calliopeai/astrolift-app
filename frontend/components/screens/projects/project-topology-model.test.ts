import { describe, expect, it } from "vitest";
import { AGENTS, APPS, WORKFLOWS, LIVE_AGENTS, WORKFLOW_RUNS } from "./projects-detail.fixtures";
import { buildProjectTopology } from "./project-topology-model";
const input = {
  agents: AGENTS,
  apps: APPS,
  workflows: WORKFLOWS,
  liveAgents: LIVE_AGENTS,
  workflowRuns: WORKFLOW_RUNS,
};
describe("project topology", () => {
  it("deduplicates repositories and only draws evidenced component relationships", () => {
    const graph = buildProjectTopology(input);
    expect(graph.nodes.filter((node) => node.id === "repository:steadymd/smd-agents")).toHaveLength(
      1
    );
    expect(
      graph.edges.some(
        (edge) => edge.source === "workflow:workflow-1" && edge.target === `agent:${AGENTS[0].id}`
      )
    ).toBe(true);
    expect(
      graph.edges.some((edge) => edge.source.startsWith("agent:") && edge.target.startsWith("app:"))
    ).toBe(false);
    expect(graph.nodes.find((node) => node.id === `agent:${AGENTS[0].id}`)?.data.status).toBe(
      "running"
    );
  });
  it("focuses one workflow and exposes useful stage links without guessing loop history", () => {
    const graph = buildProjectTopology(input, WORKFLOWS[0].guid);
    const stage = graph.nodes.find((node) => node.data.kind === "stage")!;
    expect(graph.nodes.filter((node) => node.data.kind === "workflow")).toHaveLength(1);
    expect(stage.data.href).toBe(`/agents/${AGENTS[0].slug}/build`);
    expect(stage.data.fields.find((field) => field.label === "environment")?.href).toBe(
      `/agents/environment-specs/${AGENTS[0].slug}`
    );
    expect(stage.data.status).toBe("configured");
  });
  it("shows configured return edges separately from the forward path", () => {
    const workflow = {
      ...WORKFLOWS[0],
      stages: WORKFLOWS[0].stages.map((stage, index) =>
        index === 1
          ? { ...stage, backEdge: { to: WORKFLOWS[0].stages[0].outputKey, max_rounds: 3 } }
          : stage
      ),
    };
    const graph = buildProjectTopology({ ...input, workflows: [workflow] }, workflow.guid);
    expect(graph.edges.filter((edge) => edge.label === "return")).toHaveLength(1);
    expect(graph.edges.filter((edge) => edge.label === "next")).toHaveLength(2);
  });
  it("does not turn arbitrary source text into an external URL", () => {
    const graph = buildProjectTopology({
      ...input,
      agents: [{ ...AGENTS[0], sourceRepo: "javascript:alert(1)" }],
    });
    expect(
      graph.nodes.find((node) => node.id === "repository:javascript:alert(1)")?.data.href
    ).toBeUndefined();
  });
});
