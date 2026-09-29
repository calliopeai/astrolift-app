import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  ProjectWorkflowTopology,
  WorkflowTopology,
} from "@/components/workflows/workflow-topology";
import type {
  WorkflowDefinitionSummary,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";

const meta: Meta = {
  title: "Patterns/Workflows/WorkflowTopology",
  parameters: { layout: "padded" },
};
export default meta;

const stage = (order: number, agentName: string): WorkflowTopologyStage => ({
  guid: `s${order}`,
  order,
  kind: "agent",
  role: "",
  agentRef: agentName,
  workflowRef: "",
  agentGuid: null,
  agentName,
  agentSlug: agentName,
  environmentSpecSlug: "default",
  resolvedModel: "claude-sonnet",
  hasPrompt: true,
  outputKey: agentName,
  skillRefs: [],
  fanOutCount: null,
  fanOutDynamic: false,
  onFailure: "stop",
  timeoutSeconds: 600,
});

const STAGES = [stage(1, "fetch"), stage(2, "triage-agent"), stage(3, "support-bot")];

export const Single: StoryObj = {
  render: () => (
    <WorkflowTopology stages={STAGES} statusByOrder={{ 1: "succeeded", 2: "running" }} />
  ),
};

const WORKFLOW = (slug: string, stages: WorkflowTopologyStage[]): WorkflowDefinitionSummary => ({
  guid: slug,
  name: slug,
  slug,
  description: "",
  patternKind: "sequential",
  isEnabled: true,
  isGlobal: false,
  organizationGuid: null,
  projectGuid: null,
  projectSlug: "storefront",
  projectTeamSlug: "commerce",
  sourceRepo: "",
  sourcePath: "",
  sourceRef: "",
  stageCount: stages.length,
  stages,
  createdAt: "2026-09-01T00:00:00Z",
});

export const Project: StoryObj = {
  render: () => (
    <ProjectWorkflowTopology
      workflows={[WORKFLOW("nightly-sync", STAGES), WORKFLOW("refund-flow", STAGES.slice(1))]}
      runStatusByWorkflow={{ "nightly-sync": "running" }}
    />
  ),
};
