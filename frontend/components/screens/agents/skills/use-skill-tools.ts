"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { DELETE_TOOL_DEF } from "@/graphql/agents/agents.mutations";

import { SKILL_TOOLS_LIST, selectSkillTools } from "./skill-tools-list";
import { useSkill } from "./use-skill";
import { useSkillToolDefs } from "./use-skill-tool-defs";

type DeleteToolDefData = {
  deleteToolDef: { ok: boolean; errors: { field: string; message: string; code: string }[] };
};

/**
 * A skill's Tools tab: the frame's skill (from the cache the Builder filled),
 * its tools on URL list state, and Remove. The data half of SkillToolsScreen.
 */
export function useSkillTools(id: string) {
  const skill = useSkill(id);
  const defs = useSkillToolDefs(id);
  const list = useListState(SKILL_TOOLS_LIST);
  const { state } = list;
  const { rows, totalCount } = selectSkillTools(defs.tools, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const [deleteToolDef, { loading: removing }] = useMutation<DeleteToolDefData>(DELETE_TOOL_DEF, {
    refetchQueries: ["ListToolDefs"],
  });

  // Throws on failure so ConfirmDialog holds the dialog open and toasts the
  // message, instead of closing on a remove that never happened.
  async function removeTool(toolId: string) {
    const { data } = await deleteToolDef({ variables: { id: toolId } });
    if (!data?.deleteToolDef?.ok) throw new Error("Failed to remove tool");
    toast.success("Tool removed");
  }

  return {
    id,
    skill: skill.skill,
    skillLoading: skill.loading,
    skillError: skill.error,
    onSkillRetry: skill.onRetry,
    list,
    rows,
    totalCount,
    loading: defs.loading,
    error: defs.error,
    onRetry: defs.onRetry,
    removing,
    removeTool,
  };
}

export type SkillToolsState = ReturnType<typeof useSkillTools>;
