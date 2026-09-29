"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useManifestImport,
  useManifestPreview,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { WorkflowManifestPreview } from "@/graphql/workflows/tiered.types";

import {
  buildMinimalToml,
  type NewWorkflowErrors,
  type PatternKind,
  slugify,
} from "./workflow-patterns";

export interface NewDefinitionValues {
  name: string;
  slug: string;
  description: string;
  pattern: PatternKind;
}

/** Where a manifest's parse failed, in words, with its line and path when it has them. */
function manifestError(preview: WorkflowManifestPreview): string {
  const at = [
    preview.errorLine != null
      ? `line ${preview.errorLine}${preview.errorColumn != null ? `, col ${preview.errorColumn}` : ""}`
      : null,
    preview.errorPath ? `at ${preview.errorPath}` : null,
  ].filter(Boolean);
  return `${preview.error ?? "Invalid manifest"}${at.length ? ` (${at.join(", ")})` : ""}`;
}

/**
 * Creates a workflow definition from the New workflow page: from a pattern
 * (a minimal manifest, stages added in the Builder) or from a pasted
 * manifest (validated for the Stages step first), then opens it on its
 * Builder tab. Refusals come back as field errors; the toast is only the
 * outcome. The data half of WorkflowBuilderScreen.
 */
export function useWorkflowBuilder() {
  const router = useRouter();
  const { org } = useActiveOrg();
  const { canCreate } = useWorkflowsEntitlement();
  const [importManifest] = useManifestImport();
  const [previewManifest, { loading: previewing }] = useManifestPreview();
  const [creating, setCreating] = useState(false);

  async function importToml(toml: string, onRefused: (message: string) => NewWorkflowErrors) {
    setCreating(true);
    try {
      const { data } = await importManifest({
        variables: { toml, preview: false, orgId: org?.id ?? null },
      });
      const res = data?.importWorkflowManifest;
      if (res?.ok && res.createdSlug) {
        toast.success("Workflow created", { description: res.createdSlug });
        router.push(`/workflows/${encodeURIComponent(res.createdSlug)}`);
        return {};
      }
      if (res?.manifest && !res.manifest.ok) return onRefused(manifestError(res.manifest));
      const errors = res?.errors ?? [];
      const byField = (field: string) =>
        errors.find((e) => e.field === field)?.messages.join(", ") || undefined;
      const found: NewWorkflowErrors = { name: byField("name"), slug: byField("slug") };
      if (!found.name && !found.slug) {
        found.form =
          errors.map((e) => `${e.field}: ${e.messages.join(", ")}`).join("; ") ||
          "The workflow could not be created.";
      }
      toast.error("Workflow not created");
      return found;
    } finally {
      setCreating(false);
    }
  }

  /** From a pattern: the minimal manifest, then the Builder adds stages. */
  function onCreate(values: NewDefinitionValues): Promise<NewWorkflowErrors> {
    const slug = values.slug.trim() || slugify(values.name);
    const toml = buildMinimalToml({
      slug,
      name: values.name.trim(),
      pattern: values.pattern,
      description: values.description.trim(),
    });
    return importToml(toml, (message) => ({ form: message }));
  }

  /** From a manifest, as pasted. */
  function onImport(toml: string): Promise<NewWorkflowErrors> {
    return importToml(toml, (message) => ({ toml: message }));
  }

  /** The manifest parsed for the Stages step, or its error for the field. */
  async function onPreview(
    toml: string
  ): Promise<{ preview: WorkflowManifestPreview | null; error?: string }> {
    const { data, error } = await previewManifest({ variables: { toml } });
    const preview = data?.previewWorkflowManifest ?? null;
    if (!preview)
      return { preview: null, error: error?.message ?? "The manifest could not be read." };
    return preview.ok ? { preview } : { preview: null, error: manifestError(preview) };
  }

  return { canCreate, creating, previewing, onCreate, onImport, onPreview };
}
