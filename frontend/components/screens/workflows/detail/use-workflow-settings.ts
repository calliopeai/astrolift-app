"use client";

import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useSettingsSection } from "@/components/settings/use-settings-section";
import {
  useDeleteConfiguredWorkflow,
  useDeleteDefinition,
  useUpdateConfiguredWorkflow,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { TieredMutationResult } from "@/graphql/workflows/tiered.types";

import { definitionDeletable, type FramedWorkflow } from "./use-workflow-frame";
import type { WorkflowSettingsViewProps } from "./WorkflowSettings";

function firstError(result: TieredMutationResult | undefined, fallback: string): string {
  const e = result?.errors?.[0];
  return e ? `${e.field}: ${e.messages.join(", ")}` : fallback;
}

/**
 * Data and actions for the Settings tab: the active section from the URL,
 * the name and description save (a configured workflow, gated
 * `canManage`), and delete, as the list's row actions do it (the configured
 * workflow, or an org-owned definition that no repository reconciles).
 */
export function useWorkflowSettings(framed: FramedWorkflow): WorkflowSettingsViewProps {
  const router = useRouter();
  const single = useSettingsSection();
  const { canManage } = useWorkflowsEntitlement();
  const [update, { loading: saving }] = useUpdateConfiguredWorkflow();
  const [deleteConfigured] = useDeleteConfiguredWorkflow();
  const [deleteDefinition] = useDeleteDefinition();
  const [saveError, setSaveError] = React.useState<string | null>(null);

  if (framed.kind === "definition") {
    const d = framed.definition;
    return {
      kind: "definition",
      general: { name: d.name, slug: d.slug, description: d.description },
      source: { repo: d.sourceRepo, path: d.sourcePath, ref: d.sourceRef },
      single,
      onDelete:
        canManage && definitionDeletable(d)
          ? async () => {
              const { data } = await deleteDefinition({ variables: { slug: d.slug } });
              if (!data?.deleteWorkflowDefinition?.ok) {
                toast.error(firstError(data?.deleteWorkflowDefinition, "Failed to delete"));
                return;
              }
              toast.success("Definition deleted", { description: d.name });
              router.push("/workflows");
            }
          : undefined,
    };
  }

  const w = framed.workflow;
  return {
    kind: "configured",
    general: { name: w.name, slug: w.slug, description: w.description },
    readOnly: canManage ? undefined : { permission: "workflow.update" },
    single,
    saving,
    saveError,
    onSave: async ({ name, description }) => {
      setSaveError(null);
      const { data } = await update({
        variables: { slug: w.slug, name, description, orgId: w.organizationGuid },
      });
      if (!data?.updateWorkflow?.ok) {
        setSaveError(firstError(data?.updateWorkflow, "Failed to save"));
        return false;
      }
      toast.success("Workflow saved");
      framed.refetch();
      return true;
    },
    onDelete: canManage
      ? async () => {
          const { data } = await deleteConfigured({
            variables: { slug: w.slug, orgId: w.organizationGuid },
          });
          if (!data?.deleteWorkflow?.ok) {
            toast.error(firstError(data?.deleteWorkflow, "Failed to delete workflow"));
            return;
          }
          toast.success("Workflow deleted", { description: w.name });
          router.push("/workflows");
        }
      : undefined,
  };
}
