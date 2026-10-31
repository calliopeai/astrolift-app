import type { Edge, Node } from "@xyflow/react";
import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import type {
  ConfiguredWorkflow,
  WorkflowTopologyStage,
  WorkflowDefinitionSummary,
  WorkflowDefinitionRun,
} from "@/graphql/workflows/tiered.types";
import type { ProjectDetailApp } from "./use-project-detail";

export type TopologyKind = "workflow" | "stage" | "agent" | "app" | "repository";
export type TopologyNodeData = {
  label: string;
  kind: TopologyKind;
  status: string;
  description: string;
  href?: string;
  workflowId?: string;
  fields: { label: string; value: string; href?: string }[];
} & Record<string, unknown>;
export type ProjectTopologyInput = {
  workflows: WorkflowDefinitionSummary[];
  agents: AstroliftAgentListItem[];
  apps: ProjectDetailApp[];
  liveAgents: AstroliftAgentLiveStatus[];
  workflowRuns: WorkflowDefinitionRun[];
  configuredWorkflows?: Pick<ConfiguredWorkflow, "definitionSlug" | "stageBindings">[];
};

const encode = encodeURIComponent;
export function buildProjectTopology(input: ProjectTopologyInput, focus = "") {
  const nodes: Node<TopologyNodeData>[] = [];
  const edges: Edge[] = [];
  const live = new Map(input.liveAgents.map((row) => [row.workloadId, row]));
  const latest = new Map<string, WorkflowDefinitionRun>();
  for (const run of input.workflowRuns) {
    const previous = latest.get(run.definitionGuid);
    if (!previous || (run.startedAt ?? "") > (previous.startedAt ?? ""))
      latest.set(run.definitionGuid, run);
  }
  const edge = (source: string, target: string, label: string, loop = false) => {
    const id = `${source}:${target}:${label}`;
    if (!edges.some((e) => e.id === id))
      edges.push({
        id,
        source,
        target,
        label,
        type: "smoothstep",
        style: loop ? { strokeDasharray: "6 4" } : undefined,
      });
  };
  const add = (id: string, x: number, y: number, data: TopologyNodeData) => {
    if (!nodes.some((node) => node.id === id))
      nodes.push({ id, type: "component", position: { x, y }, data });
  };
  const repository = (repo: string) => {
    if (!repo) return;
    const id = `repository:${repo}`;
    const index = nodes.filter((node) => node.data.kind === "repository").length;
    // Only known GitHub owner/repo references become external links.
    const href = /^[\w.-]+\/[\w.-]+$/.test(repo) ? `https://github.com/${repo}` : undefined;
    add(id, 0, index * 150, {
      label: repo,
      kind: "repository",
      status: "source",
      description: "",
      href,
      fields: [],
    });
    return id;
  };
  const assignedAgents = (workflow: WorkflowDefinitionSummary, stage: WorkflowTopologyStage) => {
    const ids = new Set<string>();
    for (const configured of input.configuredWorkflows ?? []) {
      if (configured.definitionSlug !== workflow.slug) continue;
      const binding = configured.stageBindings[String(stage.order)];
      if (
        binding &&
        typeof binding === "object" &&
        "agent_workload_id" in binding &&
        typeof binding.agent_workload_id === "string"
      )
        ids.add(binding.agent_workload_id);
    }
    return input.agents.filter(
      (agent) => ids.has(agent.id) || agent.slug === (stage.agentSlug || stage.agentRef)
    );
  };
  const workflows = focus ? input.workflows.filter((row) => row.guid === focus) : input.workflows;
  workflows.forEach((workflow, index) => {
    const id = `workflow:${workflow.guid}`;
    const run = latest.get(workflow.guid);
    const href = `/workflows/${encode(workflow.slug)}/observe`;
    add(id, focus ? 0 : 320, index * 180, {
      label: workflow.name,
      kind: "workflow",
      status: run?.status ?? "configured",
      description: workflow.description,
      href,
      workflowId: workflow.guid,
      fields: [
        { label: "pattern", value: workflow.patternKind },
        { label: "stages", value: String(workflow.stages.length) },
        { label: "enabled", value: String(workflow.isEnabled) },
        ...(workflow.sourceRepo ? [{ label: "repository", value: workflow.sourceRepo }] : []),
        ...(workflow.sourcePath
          ? [{ label: "source", value: `${workflow.sourceRef}:${workflow.sourcePath}` }]
          : []),
        ...(run
          ? [
              {
                label: "latestRun",
                value: run.guid,
                href: `/workflows/${encode(workflow.slug)}/runs/${encode(run.guid)}`,
              },
            ]
          : []),
      ],
    });
    if (!focus) {
      const repo = repository(workflow.sourceRepo);
      if (repo) edge(repo, id, "source");
      for (const stage of workflow.stages) {
        for (const agent of assignedAgents(workflow, stage))
          edge(id, `agent:${agent.id}`, "dispatches");
        const nested = input.workflows.find((row) => row.slug === stage.workflowRef);
        if (nested) edge(id, `workflow:${nested.guid}`, "invokes");
      }
      return;
    }
    const stages = [...workflow.stages].sort((a, b) => a.order - b.order);
    stages.forEach((stage, stageIndex) => {
      const stageId = `stage:${workflow.guid}:${stage.guid}`;
      const assignments = assignedAgents(workflow, stage);
      const agent = assignments.length === 1 ? assignments[0].slug : "";
      const agentLabel =
        assignments.map((row) => row.name).join(", ") || stage.agentSlug || stage.agentRef;
      const stageHref = stage.workflowRef
        ? `/workflows/${encode(stage.workflowRef)}/observe`
        : agent
          ? `/agents/${encode(agent)}/build`
          : `/workflows/${encode(workflow.slug)}/build`;
      // A current-stage summary cannot prove the state of earlier loop rounds.
      const status = run?.currentStageOrder === stage.order ? run.status : "configured";
      add(stageId, 320 + (stageIndex % 4) * 290, Math.floor(stageIndex / 4) * 190, {
        label: stage.role || stage.outputKey || stage.kind,
        kind: "stage",
        status,
        description: agentLabel || stage.workflowRef,
        href: stageHref,
        workflowId: workflow.guid,
        fields: [
          { label: "kind", value: stage.kind },
          { label: "order", value: String(stage.order + 1) },
          ...(assignments.length
            ? assignments.map((row) => ({
                label: "agent",
                value: row.name,
                href: `/agents/${encode(row.slug)}/build`,
              }))
            : [{ label: "agent", value: agentLabel || "—" }]),
          {
            label: "environment",
            value: stage.environmentSpecSlug || "—",
            href: stage.environmentSpecSlug
              ? `/agents/environment-specs/${encode(stage.environmentSpecSlug)}`
              : undefined,
          },
          { label: "model", value: stage.resolvedModel || "—" },
          { label: "output", value: stage.outputKey || "—" },
          { label: "skills", value: stage.skillRefs.join(", ") || "—" },
          { label: "failure", value: stage.onFailure },
          { label: "attempts", value: String(stage.maxAttempts ?? 1) },
          { label: "timeout", value: `${stage.timeoutSeconds}s` },
          { label: "workflow", value: workflow.name, href },
        ],
      });
      edge(
        stageIndex ? `stage:${workflow.guid}:${stages[stageIndex - 1].guid}` : id,
        stageId,
        "next"
      );
      const back = stage.backEdge;
      if (back && typeof back === "object" && "to" in back) {
        const target = stages.find((item) => item.outputKey === back.to || item.guid === back.to);
        if (target) edge(stageId, `stage:${workflow.guid}:${target.guid}`, "return", true);
      }
      if (stage.onFailure === "retry" && (stage.maxAttempts ?? 1) > 1)
        edge(stageId, stageId, "retry", true);
    });
  });
  if (!focus) {
    input.agents.forEach((agent, index) => {
      const status = live.get(agent.id);
      const paused = status?.isPaused ?? agent.runPaused;
      const running = status?.runningCount ?? agent.runningCount;
      const id = `agent:${agent.id}`;
      add(id, 680, index * 150, {
        label: agent.name,
        kind: "agent",
        status: paused
          ? "paused"
          : running
            ? "running"
            : (status?.lastRunStatus ?? agent.lastRunStatus ?? "unknown"),
        description: agent.slug,
        href: `/agents/${encode(agent.slug)}/observe`,
        fields: [
          { label: "mode", value: agent.runMode },
          { label: "running", value: String(running) },
          { label: "lastRun", value: status?.lastRunAt ?? agent.lastRunAt ?? "—" },
          { label: "repository", value: agent.sourceRepo || "—" },
        ],
      });
      const repo = repository(agent.sourceRepo);
      if (repo) edge(repo, id, "source");
    });
    input.apps.forEach((app, index) => {
      const id = `app:${app.id}`;
      add(id, 1040, index * 150, {
        label: app.name,
        kind: "app",
        status: app.provisioningStatus,
        description: app.slug,
        href: `/apps/${encode(app.slug)}`,
        fields: [{ label: "repository", value: app.sourceRepo || "—" }],
      });
      const repo = repository(app.sourceRepo);
      if (repo) edge(repo, id, "source");
    });
  }
  const ids = new Set(nodes.map((node) => node.id));
  return { nodes, edges: edges.filter((item) => ids.has(item.source) && ids.has(item.target)) };
}
