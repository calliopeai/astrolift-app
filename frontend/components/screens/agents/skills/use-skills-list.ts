"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useListState } from "@/components/list/use-list-state";
import { LIST_SKILLS } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { SKILLS_LIST, type SkillListItem, selectSkills } from "./skills-list";

export type { SkillListItem } from "./skills-list";

type SkillsData = { skills: SkillListItem[] };

/** `?import=1` opens the Import from repo sheet, so /agents/skills/import can land on it. */
export const IMPORT_PARAM = "import";

/**
 * The skill registry on the shared list: URL list state, the org's skills
 * and the global catalog in one read, and the page `selectSkills` cuts from
 * it. The data half of SkillsListScreen.
 */
export function useSkillsList() {
  const list = useListState(SKILLS_LIST);
  const { state } = list;
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  // Reactive org id (#agents-empty): a synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" and the query skipped.
  const { org, loading: orgLoading } = useActiveOrg();
  const orgId = org?.id ?? "";

  const { data, previousData, loading, error, refetch } = useQuery<SkillsData>(LIST_SKILLS, {
    variables: { orgId },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });
  const skills = (data ?? previousData)?.skills ?? [];
  const { rows, totalCount } = selectSkills(skills, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  function setImportOpen(open: boolean) {
    const p = new URLSearchParams(params?.toString() ?? "");
    if (open) p.set(IMPORT_PARAM, "1");
    else p.delete(IMPORT_PARAM);
    const qs = p.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  return {
    list,
    rows,
    totalCount,
    loading: (loading || orgLoading) && skills.length === 0 && !error,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    importOpen: params?.get(IMPORT_PARAM) === "1",
    onImportOpenChange: setImportOpen,
  };
}

export type SkillsListState = ReturnType<typeof useSkillsList>;
