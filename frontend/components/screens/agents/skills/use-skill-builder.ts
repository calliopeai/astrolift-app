"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import {
  CREATE_TOOL_DEF,
  DELETE_SKILL,
  DELETE_TOOL_DEF,
  UPDATE_SKILL,
} from "@/graphql/agents/agents.mutations";
import { GET_SKILL, LIST_TOOL_DEFS } from "@/graphql/agents/agents.queries";

import type { Adapter } from "./tool-adapters";

// ─── Mutation response types ──────────────────────────────────────────────────

type MutError = { field: string; message: string; code: string };

type UpdateSkillData = {
  updateSkill: { ok: boolean; errors: MutError[]; data: { id: string; slug: string } | null };
};

type DeleteSkillData = {
  deleteSkill: { ok: boolean; errors: MutError[] };
};

type CreateToolDefData = {
  createToolDef: { ok: boolean; errors: MutError[]; data: { id: string } | null };
};

type DeleteToolDefData = {
  deleteToolDef: { ok: boolean; errors: MutError[] };
};

// ─── Types ────────────────────────────────────────────────────────────────────

export type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string;
  content: string;
  skillVersion: number;
  isGlobal: boolean;
  isActive: boolean;
};

export type ToolDef = {
  id: string;
  name: string;
  slug: string;
  description: string;
  adapter: string;
  handlerRef: string;
  inputSchema: unknown;
  outputSchema: unknown;
  createdAt: string;
};

type SkillData = { skill: Skill | null };
type ToolDefsData = { toolDefs: ToolDef[] };

export type SkillFields = {
  name: string;
  slug: string;
  description: string;
  content: string;
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

/**
 * One skill, its tool definitions, and every mutation the skill builder
 * runs (save, delete, register / remove tool, AI assist). The data half
 * of SkillBuilderScreen.
 */
export function useSkillBuilder(id: string) {
  const router = useRouter();
  const [aiAssisting, setAiAssisting] = useState(false);

  const {
    data: skillData,
    loading: skillLoading,
    error: skillError,
  } = useQuery<SkillData>(GET_SKILL, {
    variables: { id },
    fetchPolicy: "cache-and-network",
    skip: !id,
  });

  const { data: toolsData, loading: toolsLoading } = useQuery<ToolDefsData>(LIST_TOOL_DEFS, {
    variables: { skillId: id },
    fetchPolicy: "cache-and-network",
    skip: !id,
  });

  const [updateSkill, { loading: saving }] = useMutation<UpdateSkillData>(UPDATE_SKILL, {
    refetchQueries: ["GetSkill"],
  });

  const [deleteSkillMutation, { loading: deleting }] = useMutation<DeleteSkillData>(DELETE_SKILL, {
    refetchQueries: ["ListSkills"],
  });

  const [deleteToolDef, { loading: deletingTool }] = useMutation<DeleteToolDefData>(
    DELETE_TOOL_DEF,
    { refetchQueries: ["ListToolDefs"] }
  );

  const [createToolDef, { loading: creatingTool }] = useMutation<CreateToolDefData>(
    CREATE_TOOL_DEF,
    { refetchQueries: ["ListToolDefs"] }
  );

  /** Resolves true when the skill saved, so the form can clear its dirty flag. */
  async function saveSkill(fields: SkillFields): Promise<boolean> {
    if (!fields.name.trim() || !fields.slug.trim()) {
      toast.error("Name and slug are required");
      return false;
    }
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
      return true;
    }
    for (const err of data?.updateSkill?.errors ?? []) {
      toast.error(`${err.field}: ${err.message}`);
    }
    return false;
  }

  // Both destructive handlers throw on failure: ConfirmDialog keeps the
  // dialog open and surfaces the message as a toast, so a failed delete
  // stays correctable instead of dismissing itself.
  async function deleteSkill() {
    const { data } = await deleteSkillMutation({ variables: { id } });
    if (data?.deleteSkill?.ok) {
      toast.success("Skill deleted");
      router.push("/agents/skills");
    } else {
      throw new Error("Failed to delete skill");
    }
  }

  async function deleteTool(toolId: string) {
    const { data } = await deleteToolDef({ variables: { id: toolId } });
    if (!data?.deleteToolDef?.ok) {
      throw new Error("Failed to remove tool");
    }
  }

  /** Resolves true when the tool registered, so the form can close. */
  async function createTool(fields: ToolDefFields): Promise<boolean> {
    if (!fields.name.trim() || !fields.slug.trim()) {
      toast.error("Name and slug are required");
      return false;
    }
    let parsedInput: unknown = {};
    let parsedOutput: unknown = {};
    try {
      parsedInput = JSON.parse(fields.inputSchemaText);
    } catch {
      toast.error("Input schema is not valid JSON");
      return false;
    }
    try {
      parsedOutput = JSON.parse(fields.outputSchemaText);
    } catch {
      toast.error("Output schema is not valid JSON");
      return false;
    }
    const { data } = await createToolDef({
      variables: {
        skillId: id,
        input: {
          name: fields.name.trim(),
          slug: fields.slug.trim(),
          description: fields.description.trim(),
          adapter: fields.adapter,
          handlerRef: fields.handlerRef.trim(),
          inputSchema: parsedInput,
          outputSchema: parsedOutput,
          implementationConfig: null,
        },
      },
    });
    if (data?.createToolDef?.ok) {
      toast.success("Tool registered");
      return true;
    }
    for (const err of data?.createToolDef?.errors ?? []) {
      toast.error(`${err.field}: ${err.message}`);
    }
    return false;
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
        toast.success("AI-generated content applied — review before saving");
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
    skill: skillData?.skill ?? null,
    tools: toolsData?.toolDefs ?? [],
    skillLoading,
    toolsLoading,
    errorMessage: skillError ? skillError.message : null,
    saving,
    deleting,
    deletingTool,
    creatingTool,
    aiAssisting,
    saveSkill,
    deleteSkill,
    deleteTool,
    createTool,
    aiAssist,
  };
}

export type SkillBuilderState = ReturnType<typeof useSkillBuilder>;
