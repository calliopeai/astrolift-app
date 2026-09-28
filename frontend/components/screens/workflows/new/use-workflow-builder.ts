"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useManifestImport, useWorkflowsEntitlement } from "@/graphql/workflows/tiered.hooks";

import { buildMinimalToml, slugify, type PatternKind } from "./workflow-patterns";

export interface NewDefinitionValues {
  name: string;
  slug: string;
  description: string;
  pattern: PatternKind;
}

/**
 * Creates a workflow definition from the builder's metadata + pattern by
 * importing a minimal manifest, then opens the stage builder. The data half
 * of WorkflowBuilderScreen.
 */
export function useWorkflowBuilder() {
  const router = useRouter();
  const { org } = useActiveOrg();
  const { canCreate } = useWorkflowsEntitlement();
  const [importManifest] = useManifestImport();
  const [creating, setCreating] = useState(false);

  async function onCreate(values: NewDefinitionValues): Promise<void> {
    const { name, slug, description, pattern } = values;
    const finalSlug = slug.trim() || slugify(name);
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (!finalSlug) {
      toast.error("Slug is required");
      return;
    }

    setCreating(true);
    try {
      const toml = buildMinimalToml({
        slug: finalSlug,
        name: name.trim(),
        pattern,
        description: description.trim(),
      });
      const { data } = await importManifest({
        variables: { toml, preview: false, orgId: org?.id ?? null },
      });
      const res = data?.importWorkflowManifest;
      if (res?.ok && res.createdSlug) {
        toast.success("Definition created", { description: res.createdSlug });
        router.push(`/workflows/${res.createdSlug}/builder`);
        return;
      }
      const errors = res?.errors ?? [];
      if (errors.length > 0) {
        for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
      } else if (res?.manifest?.error) {
        toast.error(res.manifest.error);
      } else {
        toast.error("Failed to create the definition");
      }
    } finally {
      setCreating(false);
    }
  }

  return { canCreate, creating, onCreate };
}
