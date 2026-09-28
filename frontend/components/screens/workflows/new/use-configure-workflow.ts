"use client";

import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { useAgentWorkloadOptions } from "@/components/workflows/use-picker-options";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useCreateConfiguredWorkflow,
  useWorkflowDefinition,
  useWorkflowStages,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { WorkflowStage } from "@/graphql/workflows/tiered.types";

export type TriggerKind = "manual" | "schedule";

export interface ConfigureWorkflowValues {
  name: string;
  /** Stage order -> picked agent workload id (null when cleared). */
  bindings: Record<number, string | null>;
  inputsText: string;
  triggerKind: TriggerKind;
  scheduleCron: string;
}

/**
 * Agent stages with no agent: an agent_dispatch stage is bound when the
 * operator picked a workload here or the definition's stage carries its own
 * default agent.
 */
export function findUnboundStages(
  stages: WorkflowStage[],
  bindings: Record<number, string | null>
): WorkflowStage[] {
  return stages.filter(
    (s) => s.kind === "agent_dispatch" && !bindings[s.order] && !s.agentDefinitionGuid
  );
}

/**
 * The definition (template) behind `/workflows/new?definition=<slug>`, its
 * stages, the org's agent workloads, and the create mutation that turns it
 * into a configured workflow. The data half of ConfigureWorkflowScreen.
 */
export function useConfigureWorkflow(definitionSlug: string) {
  const router = useRouter();
  const { org } = useActiveOrg();
  const orgScoped = org?.id ?? null;
  const agentWorkloads = useAgentWorkloadOptions(orgScoped);
  const { canCreate, loading: entitlementLoading } = useWorkflowsEntitlement();
  const { definition, loading: definitionLoading, error } = useWorkflowDefinition(definitionSlug);
  const { stages, loading: stagesLoading } = useWorkflowStages(definitionSlug);
  const [createWorkflow, { loading: submitting }] = useCreateConfiguredWorkflow();

  const loading = definitionLoading || stagesLoading;
  const orderedStages = [...stages].sort((a, b) => a.order - b.order);

  async function onSubmit(values: ConfigureWorkflowValues): Promise<void> {
    const { name, bindings, inputsText, triggerKind, scheduleCron } = values;
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (findUnboundStages(orderedStages, bindings).length > 0) return;

    let inputs: Record<string, unknown> | undefined;
    if (inputsText.trim()) {
      try {
        inputs = JSON.parse(inputsText);
      } catch {
        toast.error("Inputs must be valid JSON");
        return;
      }
    }

    const stageBindings: Record<string, { agent_workload_id: string }> = {};
    for (const [order, workloadId] of Object.entries(bindings)) {
      if (workloadId) stageBindings[order] = { agent_workload_id: workloadId };
    }

    const { data } = await createWorkflow({
      variables: {
        name: name.trim(),
        definitionSlug,
        stageBindings,
        inputs,
        triggerKind,
        scheduleCron: triggerKind === "schedule" ? scheduleCron : undefined,
      },
    });

    const result = data?.createWorkflow;
    if (result?.ok && result.workflow) {
      toast.success("Workflow created", {
        description: `${result.workflow.name} is configured and ready to run.`,
      });
      router.push(`/workflows/${result.workflow.slug}`);
    } else {
      const errors = result?.errors ?? [];
      if (errors.length > 0) {
        for (const err of errors) {
          toast.error(`${err.field}: ${err.messages.join(", ")}`);
        }
      } else {
        toast.error("Failed to create workflow");
      }
    }
  }

  return {
    definitionSlug,
    definition,
    orderedStages,
    loading,
    error,
    canCreate,
    entitlementLoading,
    orgScoped,
    agentWorkloads,
    submitting,
    onSubmit,
  };
}
