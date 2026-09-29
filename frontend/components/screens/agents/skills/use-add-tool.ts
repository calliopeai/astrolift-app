"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { CREATE_TOOL_DEF } from "@/graphql/agents/agents.mutations";

import { type FieldErrors, hasErrors, splitErrors } from "./catalog";
import { skillTabHref } from "./SkillFrame";
import type { Adapter } from "./tool-adapters";
import { useSkill } from "./use-skill";

type CreateToolDefData = {
  createToolDef: {
    ok: boolean;
    errors: { field: string; message: string; code: string }[];
    data: { id: string } | null;
  };
};

export type ToolDefFields = {
  name: string;
  slug: string;
  description: string;
  adapter: Adapter;
  handlerRef: string;
  inputSchemaText: string;
  outputSchemaText: string;
};

export const TOOL_FIELDS = [
  "name",
  "slug",
  "description",
  "adapter",
  "handlerRef",
  "inputSchema",
  "outputSchema",
] as const;
export type ToolField = (typeof TOOL_FIELDS)[number];
export type ToolErrors = FieldErrors<ToolField>;

/** Which step of Register tool holds a field, so a refusal opens where its error is. */
export const TOOL_STEP: Record<ToolField, 1 | 2 | 3> = {
  name: 1,
  slug: 1,
  description: 1,
  adapter: 2,
  handlerRef: 2,
  inputSchema: 3,
  outputSchema: 3,
};

function parse(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(text.trim() || "{}") };
  } catch {
    return { ok: false };
  }
}

/** The checks the page runs before it asks the server, beside their fields. */
export function validateTool(fields: ToolDefFields): ToolErrors {
  const e: ToolErrors = {};
  if (!fields.name.trim()) e.name = "Give the tool a name.";
  if (!fields.slug.trim()) e.slug = "A slug is required.";
  if (!parse(fields.inputSchemaText).ok) e.inputSchema = "Input schema is not valid JSON.";
  if (!parse(fields.outputSchemaText).ok) e.outputSchema = "Output schema is not valid JSON.";
  return e;
}

/**
 * Register a tool on a skill: the skill for the header, and the create
 * mutation. Refusals come back as errors for the page to show in place; the
 * only toast is the outcome, and the page then goes to the skill's Tools
 * tab. The data half of AddToolScreen.
 */
export function useAddTool(skillId: string) {
  const router = useRouter();
  const skill = useSkill(skillId);
  const [createToolDef, { loading }] = useMutation<CreateToolDefData>(CREATE_TOOL_DEF, {
    refetchQueries: ["ListToolDefs", "ListOrgToolDefs", "ToolDefsListPage"],
  });

  async function createTool(fields: ToolDefFields): Promise<ToolErrors> {
    const invalid = validateTool(fields);
    if (hasErrors(invalid)) return invalid;
    const input = parse(fields.inputSchemaText);
    const output = parse(fields.outputSchemaText);
    try {
      const { data } = await createToolDef({
        variables: {
          skillId,
          input: {
            name: fields.name.trim(),
            slug: fields.slug.trim(),
            description: fields.description.trim(),
            adapter: fields.adapter,
            handlerRef: fields.handlerRef.trim(),
            inputSchema: input.ok ? input.value : {},
            outputSchema: output.ok ? output.value : {},
            implementationConfig: null,
          },
        },
      });
      if (data?.createToolDef?.ok) {
        toast.success("Tool registered");
        router.push(skillTabHref(skillId, "tools"));
        return {};
      }
      const errors = splitErrors(data?.createToolDef?.errors ?? [], TOOL_FIELDS);
      return hasErrors(errors) ? errors : { form: "The tool was not registered." };
    } catch (err) {
      return { form: err instanceof Error ? err.message : String(err) };
    }
  }

  return {
    skillId,
    skill: skill.skill,
    skillLoading: skill.loading,
    skillError: skill.error,
    creating: loading,
    createTool,
  };
}

export type AddToolState = ReturnType<typeof useAddTool>;
