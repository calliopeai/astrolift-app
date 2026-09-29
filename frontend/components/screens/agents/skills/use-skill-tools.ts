"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { DELETE_TOOL_DEF } from "@/graphql/agents/agents.mutations";
import { TOOL_DEFS_LIST_PAGE } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { SKILL_TOOLS_LIST, type SkillToolRow, skillToolsPageVariables } from "./skill-tools-list";
import { useSkill } from "./use-skill";

type ToolDefsPageData = {
  orgToolDefsPage: { items: SkillToolRow[]; totalCount: number };
};

type DeleteToolDefData = {
  deleteToolDef: { ok: boolean; errors: { field: string; message: string; code: string }[] };
};

/**
 * A skill's Tools tab: the frame's skill (from the cache the Builder filled),
 * one numbered page of its tools from `orgToolDefsPage` (narrowed to the
 * skill once it has loaded, #2155), and Remove. The data half of
 * SkillToolsScreen.
 */
export function useSkillTools(id: string) {
  const skill = useSkill(id);
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const list = useListState(SKILL_TOOLS_LIST);
  const { state } = list;
  const owner = skill.skill;
  const page = useQuery<ToolDefsPageData>(TOOL_DEFS_LIST_PAGE, {
    variables: {
      orgId,
      ...skillToolsPageVariables(
        { slug: owner?.slug ?? "", isGlobal: owner?.isGlobal ?? false },
        {
          q: state.q,
          filters: list.filters,
          sort: state.sort,
          page: state.page,
          pageSize: state.pageSize,
        }
      ),
    },
    fetchPolicy: "cache-and-network",
    skip: !orgId || !owner,
  });
  const shown = page.data ?? page.previousData;
  const rows = shown?.orgToolDefsPage.items ?? [];

  const [deleteToolDef, { loading: removing }] = useMutation<DeleteToolDefData>(DELETE_TOOL_DEF, {
    refetchQueries: ["ListToolDefs", "ToolDefsListPage"],
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
    totalCount: shown?.orgToolDefsPage.totalCount ?? rows.length,
    // The tools wait on the skill: its slug and scope narrow the page.
    loading: !shown && !page.error && (skill.loading || page.loading || !orgId),
    error: page.error ? { message: page.error.message } : null,
    onRetry: () => {
      void page.refetch();
    },
    removing,
    removeTool,
  };
}

export type SkillToolsState = ReturnType<typeof useSkillTools>;
