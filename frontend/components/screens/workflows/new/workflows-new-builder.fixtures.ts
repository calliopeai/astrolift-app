import { DEFINITION, STAGES, ORG_ID, WORKLOADS } from "@/components/workflows/fixtures";
import type { WorkflowStage } from "@/graphql/workflows/tiered.types";

import type { ConfigureWorkflowScreenProps } from "./ConfigureWorkflowScreen";
import type { WorkflowBuilderScreenProps } from "./WorkflowBuilderScreen";

/** Hand-typed props for the configure-to-run form and the new-definition builder. */

const noop = async () => {};

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
  onSubmit: noop,
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

export const BUILDER: WorkflowBuilderScreenProps = {
  canCreate: true,
  creating: false,
  onCreate: noop,
  initialPattern: "single",
};

export const LONG_BUILDER_NAME = LONG_NAME;
