"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useCloneDefinition,
  useWorkflowDefinitions,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { WorkflowDefinitionSummary } from "@/graphql/workflows/tiered.types";

/**
 * The global workflow templates behind /workflows/templates and the clone
 * mutation. The data half of WorkflowTemplatesScreen.
 */
export function useWorkflowTemplates() {
  const router = useRouter();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? null;
  const { definitions, loading, error } = useWorkflowDefinitions(orgId);
  const { canCreate } = useWorkflowsEntitlement();
  const [cloneDefinition] = useCloneDefinition();
  // Slugs with a clone in flight, so each card shows its own spinner.
  const [cloningSlugs, setCloningSlugs] = useState<string[]>([]);

  const templates = definitions.filter((d) => d.isGlobal);

  const onClone = async (definition: WorkflowDefinitionSummary) => {
    setCloningSlugs((s) => [...s, definition.slug]);
    try {
      const { data } = await cloneDefinition({
        variables: { slug: definition.slug, orgId },
      });
      const result = data?.cloneWorkflowDefinition;
      if (result?.ok && result.slug) {
        toast.success(`Cloned "${definition.name}"`, {
          description: "Your editable copy is ready in the builder.",
        });
        router.push(`/workflows/${result.slug}/builder`);
      } else {
        const errors = result?.errors ?? [];
        if (errors.length > 0) {
          for (const e of errors) {
            toast.error(`${e.field}: ${e.messages.join(", ")}`);
          }
        } else {
          toast.error("Failed to clone template");
        }
      }
    } finally {
      setCloningSlugs((s) => s.filter((slug) => slug !== definition.slug));
    }
  };

  return { templates, loading, error, canCreate, cloningSlugs, onClone };
}
