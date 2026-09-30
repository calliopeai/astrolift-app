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

export interface ConfigureErrors {
  name?: string;
  scheduleCron?: string;
  inputs?: string;
  /** Stage order -> why it cannot run as bound. */
  stages?: Record<number, string>;
  /** The submit failed for a reason no field owns. */
  form?: string;
}

/** Workflow-level inputs as typed: empty is none; anything else must be a JSON object. */
export function parseInputs(text: string): { inputs?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return {};
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value))
      return { error: "Inputs must be a JSON object." };
    return { inputs: value as Record<string, unknown> };
  } catch {
    return { error: "Inputs must be valid JSON." };
  }
}

/**
 * The errors for one step of the Configure page (spec 44 §5.4), beside their
 * fields: Source holds the name, trigger and inputs; Stages the bindings.
 * Empty when the step may continue.
 */
export function validateConfigure(
  step: "source" | "stages",
  values: ConfigureWorkflowValues,
  stages: WorkflowStage[]
): ConfigureErrors {
  const errors: ConfigureErrors = {};
  if (step === "source") {
    if (!values.name.trim()) errors.name = "Give the workflow a name.";
    if (values.triggerKind === "schedule" && !values.scheduleCron.trim())
      errors.scheduleCron = "A schedule needs a cron expression.";
    const parsed = parseInputs(values.inputsText);
    if (parsed.error) errors.inputs = parsed.error;
    return errors;
  }
  const unbound = findUnboundStages(stages, values.bindings);
  if (unbound.length > 0)
    errors.stages = Object.fromEntries(
      unbound.map((s) => [s.order, "Bind an agent: this stage has no default."])
    );
  return errors;
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
 * into a configured workflow. A refusal comes back as field errors; the
 * toast is only the outcome. The data half of ConfigureWorkflowScreen.
 */
export function useConfigureWorkflow(definitionSlug: string) {
  const router = useRouter();
  const { org } = useActiveOrg();
  const orgScoped = org?.id ?? null;
  const agentWorkloads = useAgentWorkloadOptions(orgScoped);
  const { canCreate, loading: entitlementLoading } = useWorkflowsEntitlement();
  const {
    definition,
    loading: definitionLoading,
    error,
  } = useWorkflowDefinition(orgScoped ? definitionSlug : null, orgScoped);
  const { stages, loading: stagesLoading } = useWorkflowStages(orgScoped ? definitionSlug : null);
  const [createWorkflow, { loading: submitting }] = useCreateConfiguredWorkflow();

  const loading = definitionLoading || stagesLoading;
  const orderedStages = [...stages].sort((a, b) => a.order - b.order);

  async function onSubmit(values: ConfigureWorkflowValues): Promise<ConfigureErrors> {
    const { name, bindings, inputsText, triggerKind, scheduleCron } = values;
    const found = {
      ...validateConfigure("source", values, orderedStages),
      ...validateConfigure("stages", values, orderedStages),
    };
    if (Object.keys(found).length > 0) return found;

    const stageBindings: Record<string, { agent_workload_id: string }> = {};
    for (const [order, workloadId] of Object.entries(bindings)) {
      if (workloadId) stageBindings[order] = { agent_workload_id: workloadId };
    }

    const { data } = await createWorkflow({
      variables: {
        name: name.trim(),
        definitionSlug,
        stageBindings,
        inputs: parseInputs(inputsText).inputs,
        triggerKind,
        scheduleCron: triggerKind === "schedule" ? scheduleCron : undefined,
      },
    });

    const result = data?.createWorkflow;
    if (result?.ok && result.workflow) {
      toast.success("Workflow created", {
        description: `${result.workflow.name} is configured and ready to run.`,
      });
      router.push(`/workflows/${encodeURIComponent(result.workflow.slug)}`);
      return {};
    }
    toast.error("Workflow not created");
    const errors = result?.errors ?? [];
    const byField = (field: string) =>
      errors.find((e) => e.field === field)?.messages.join(", ") || undefined;
    const refused: ConfigureErrors = {
      name: byField("name"),
      scheduleCron: byField("scheduleCron") ?? byField("schedule_cron"),
      inputs: byField("inputs"),
    };
    if (!refused.name && !refused.scheduleCron && !refused.inputs)
      refused.form =
        errors.map((e) => `${e.field}: ${e.messages.join(", ")}`).join("; ") ||
        "The workflow could not be created.";
    return refused;
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
