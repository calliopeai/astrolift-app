"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { DELETE_SKILL, UPDATE_SKILL } from "@/graphql/agents/agents.mutations";

import { type FieldErrors, hasErrors, splitErrors } from "./catalog";
import { useSkill } from "./use-skill";
import { useSkillToolDefs } from "./use-skill-tool-defs";

export type { Skill } from "./use-skill";
export type { ToolDef } from "./use-skill-tool-defs";

type MutError = { field: string; message: string; code: string };

type UpdateSkillData = {
  updateSkill: { ok: boolean; errors: MutError[]; data: { id: string; slug: string } | null };
};

type DeleteSkillData = {
  deleteSkill: { ok: boolean; errors: MutError[] };
};

export type SkillFields = {
  name: string;
  slug: string;
  description: string;
  content: string;
};

export const SKILL_FIELDS = ["name", "slug", "description", "content"] as const;
export type SkillErrors = FieldErrors<(typeof SKILL_FIELDS)[number]>;

/** Name and slug are required; checked before the save, beside their fields. */
export function validateSkill(fields: Pick<SkillFields, "name" | "slug">): SkillErrors {
  const e: SkillErrors = {};
  if (!fields.name.trim()) e.name = "Give the skill a name.";
  if (!fields.slug.trim()) e.slug = "A slug is required.";
  return e;
}

/**
 * The skill builder's data: the skill, its tools for the summary panel, and
 * save, delete and AI assist. The tools themselves are edited on the Tools
 * tab. The data half of SkillBuilderScreen.
 */
export function useSkillBuilder(id: string) {
  const router = useRouter();
  const [aiAssisting, setAiAssisting] = useState(false);
  const skill = useSkill(id);
  const tools = useSkillToolDefs(id);

  const [updateSkill, { loading: saving }] = useMutation<UpdateSkillData>(UPDATE_SKILL, {
    refetchQueries: ["GetSkill"],
  });

  const [deleteSkillMutation, { loading: deleting }] = useMutation<DeleteSkillData>(DELETE_SKILL, {
    refetchQueries: ["ListSkills"],
  });

  /** Resolves the errors to show beside their fields; empty when it saved. */
  async function saveSkill(fields: SkillFields): Promise<SkillErrors> {
    const invalid = validateSkill(fields);
    if (hasErrors(invalid)) return invalid;
    try {
      const { data } = await updateSkill({
        variables: {
          id,
          input: {
            name: fields.name.trim(),
            slug: fields.slug.trim(),
            description: fields.description.trim(),
            content: fields.content.trim(),
            dependencies: null,
          },
        },
      });
      if (data?.updateSkill?.ok) {
        toast.success("Skill saved");
        return {};
      }
      const errors = splitErrors(data?.updateSkill?.errors ?? [], SKILL_FIELDS);
      return hasErrors(errors) ? errors : { form: "The skill was not saved." };
    } catch (err) {
      return { form: err instanceof Error ? err.message : String(err) };
    }
  }

  // Throws on failure: ConfirmDialog keeps the dialog open and surfaces the
  // message as a toast, so a failed delete stays correctable instead of
  // dismissing itself.
  async function deleteSkill() {
    const { data } = await deleteSkillMutation({ variables: { id } });
    if (data?.deleteSkill?.ok) {
      toast.success("Skill deleted");
      router.push("/agents/skills");
    } else {
      throw new Error("Failed to delete skill");
    }
  }

  /** Resolves the generated content, or null when there is nothing to apply. */
  async function aiAssist(fields: {
    name: string;
    description: string;
    content: string;
  }): Promise<string | null> {
    if (!fields.description.trim() && !fields.name.trim()) {
      toast.error("Add a name or description first so the AI has context");
      return null;
    }
    setAiAssisting(true);
    try {
      const res = await fetch("/api/agents/v1/skills/ai-assist/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-platform": "web" },
        body: JSON.stringify({
          name: fields.name.trim(),
          description: fields.description.trim(),
          content: fields.content,
        }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const json = await res.json();
      if (json.content) {
        toast.success("AI-generated content applied. Review before saving.");
        return json.content as string;
      }
      return null;
    } catch (err) {
      toast.error(`AI assist failed: ${err instanceof Error ? err.message : String(err)}`);
      return null;
    } finally {
      setAiAssisting(false);
    }
  }

  return {
    id,
    skill: skill.skill,
    skillLoading: skill.loading,
    errorMessage: skill.error,
    onRetry: skill.onRetry,
    tools: tools.tools,
    toolsLoading: tools.loading,
    toolsError: tools.error,
    onToolsRetry: tools.onRetry,
    saving,
    deleting,
    aiAssisting,
    saveSkill,
    deleteSkill,
    aiAssist,
  };
}

export type SkillBuilderState = ReturnType<typeof useSkillBuilder>;
