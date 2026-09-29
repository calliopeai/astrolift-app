import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import type { WorkflowDefinitionSummary, WorkflowStage } from "@/graphql/workflows/tiered.types";

import type { SkillOption } from "./pickers";
import type { StageBuilderProps } from "./StageBuilder";

/** Hand-typed catalog fixtures for the workflow builder and its pickers. */

export const ORG_ID = "org-7f3c2a10";

const agent = (id: string, name: string, appSlug: string) =>
  ({ id, name, slug: name, appSlug }) as AstroliftAgentListItem;

export const WORKLOADS: AstroliftAgentListItem[] = [
  agent("wl-bdr", "bdr-outreach", "sales-agents"),
  agent("wl-research", "account-research", "sales-agents"),
  agent("wl-review", "contract-review-with-a-deliberately-long-name", "legal"),
];

export const SKILLS: SkillOption[] = [
  { slug: "crm-lookup", name: "CRM lookup", isGlobal: false },
  { slug: "web-research", name: "Web research", isGlobal: true },
  { slug: "email-draft", name: "Email draft", isGlobal: false },
];

const stage = (order: number, kind: string, patch: Partial<WorkflowStage> = {}): WorkflowStage => ({
  guid: `stage-${order}`,
  order,
  kind,
  role: "",
  prompt: "",
  approvers: [],
  agentRef: "",
  workflowRef: "",
  environmentSpecSlug: "",
  outputKey: "",
  skillRefs: [],
  fanOutCount: null,
  onFailure: "fail",
  timeoutSeconds: 300,
  createdAt: "2026-09-20T10:00:00Z",
  agentDefinitionGuid: null,
  agentDefinitionName: null,
  ...patch,
});

export const STAGES: WorkflowStage[] = [
  stage(0, "agent_dispatch", {
    role: "research",
    agentDefinitionGuid: "wl-research",
    agentDefinitionName: "account-research",
    skillRefs: ["web-research", "crm-lookup"],
    fanOutCount: 3,
    outputKey: "accounts",
  }),
  stage(1, "aggregation", { role: "merge" }),
  stage(2, "human_gate", { role: "approve", approvers: ["sales-lead@example.com"] }),
  stage(3, "agent_dispatch", {
    role: "outreach",
    agentDefinitionGuid: "wl-bdr",
    agentDefinitionName: "bdr-outreach",
    skillRefs: ["email-draft"],
  }),
];

export const DEFINITION: WorkflowDefinitionSummary = {
  guid: "def-1",
  name: "Outbound pipeline",
  slug: "outbound-pipeline",
  description: "Research accounts in parallel, merge, get sign-off, then send.",
  patternKind: "pipeline",
  isEnabled: true,
  isGlobal: false,
  organizationGuid: ORG_ID,
  projectGuid: null,
  projectSlug: "",
  projectTeamSlug: "",
  sourceRepo: "",
  sourcePath: "",
  sourceRef: "",
  stageCount: STAGES.length,
  stages: [],
  createdAt: "2026-09-20T10:00:00Z",
};

const MANIFEST = `[workflow]
name = "Outbound pipeline"
pattern = "pipeline"

[[stages]]
kind = "agent_dispatch"
role = "research"
fan_out = 3
`;

export const BUILDER: StageBuilderProps = {
  slug: DEFINITION.slug,
  orgId: ORG_ID,
  canCreate: true,
  canManage: true,
  definition: DEFINITION,
  defLoading: false,
  stages: STAGES,
  stagesLoading: false,
  busyGuid: null,
  creating: false,
  cloning: false,
  onSaveStage: async () => {},
  onDeleteStage: async () => {},
  onMoveStage: async () => {},
  onCreateStage: async () => true,
  onClone: async () => {},
  manifest: {
    load: () => {},
    exported: { ok: true, toml: MANIFEST, error: null },
    loading: false,
    error: null,
    preview: async () => null,
    importAsNew: async () => null,
  },
  pickerOptions: {
    workloads: WORKLOADS,
    workloadsLoading: false,
    skills: SKILLS,
    skillsLoading: false,
  },
};
