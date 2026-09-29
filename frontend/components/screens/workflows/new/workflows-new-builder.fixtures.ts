import { DEFINITION, STAGES, ORG_ID, WORKLOADS } from "@/components/workflows/fixtures";
import type {
  WorkflowManifestPreview,
  WorkflowManifestStage,
  WorkflowStage,
} from "@/graphql/workflows/tiered.types";

import type { ConfigureWorkflowScreenProps } from "./ConfigureWorkflowScreen";
import type { WorkflowBuilderScreenProps } from "./WorkflowBuilderScreen";

/** Hand-typed props for the configure-to-run form and the new-definition builder. */

const manifestStage = (
  order: number,
  kind: string,
  patch: Partial<WorkflowManifestStage> = {}
): WorkflowManifestStage => ({
  order,
  kind,
  role: "",
  agent: null,
  workflow: null,
  environmentSpecSlug: null,
  skills: [],
  onFailure: "fail",
  timeout: 300,
  fanOut: "",
  prompt: null,
  outputKey: null,
  approvers: [],
  ...patch,
});

export const CONFIGURE: ConfigureWorkflowScreenProps = {
  definitionSlug: DEFINITION.slug,
  definition: DEFINITION,
  orderedStages: STAGES,
  loading: false,
  error: undefined,
  canCreate: true,
  entitlementLoading: false,
  orgScoped: ORG_ID,
  agentWorkloads: { workloads: WORKLOADS, loading: false },
  submitting: false,
  onSubmit: async () => ({}),
};

/** Stage 3 has no default agent, so the form asks for a binding before it can submit. */
export const UNBOUND_STAGES: WorkflowStage[] = STAGES.map((s) =>
  s.order === 3 ? { ...s, agentDefinitionGuid: null, agentDefinitionName: null } : s
);

const LONG_NAME =
  "Quarterly enterprise account research, enrichment, legal review and multi-region outbound pipeline";

export const LONG_CONFIGURE: ConfigureWorkflowScreenProps = {
  ...CONFIGURE,
  definition: {
    ...DEFINITION,
    name: LONG_NAME,
    description:
      "Research every enterprise account in the territory in parallel across all regions, merge and deduplicate the findings, route them through legal and the regional sales lead for sign-off, then send individually drafted outreach from the owning rep's mailbox.",
  },
  orderedStages: STAGES.map((s) =>
    s.order === 0
      ? { ...s, agentDefinitionName: "contract-review-with-a-deliberately-long-name" }
      : s
  ),
};

/** A manifest that parsed: fan-out, merge, a gate that retries, then a nested workflow. */
export const MANIFEST_PREVIEW: WorkflowManifestPreview = {
  ok: true,
  error: null,
  errorPath: null,
  errorLine: null,
  errorColumn: null,
  definition: {
    slug: "outbound-pipeline",
    name: "Outbound pipeline",
    pattern: "fan_out",
    description: "Research accounts in parallel, merge, get sign-off, then send.",
  },
  stages: [
    manifestStage(0, "agent_dispatch", {
      role: "research",
      agent: "account-research",
      fanOut: "3",
    }),
    manifestStage(1, "aggregation", { role: "merge" }),
    manifestStage(2, "human_gate", { role: "approve", onFailure: "retry" }),
    manifestStage(3, "workflow", { role: "send", workflow: "bdr-outreach" }),
  ],
};

export const BUILDER: WorkflowBuilderScreenProps = {
  canCreate: true,
  creating: false,
  previewing: false,
  onCreate: async () => ({}),
  onImport: async () => ({}),
  onPreview: async () => ({ preview: MANIFEST_PREVIEW }),
  initialPattern: "single",
};

export const LONG_BUILDER_NAME = LONG_NAME;
