"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useListState } from "@/components/list/use-list-state";
import { SKILLS_LIST_PAGE } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { SKILLS_LIST, type SkillListItem, skillsPageVariables } from "./skills-list";

export type { SkillListItem } from "./skills-list";

type SkillsData = {
  skillsPage: {
    items: SkillListItem[];
    totalCount: number;
    page: number;
    pageSize: number;
  };
};

/** `?import=1` opens the Import from repo sheet, so /agents/skills/import can land on it. */
export const IMPORT_PARAM = "import";

/**
 * The skill registry on the shared list: URL list state in, one numbered
 * page of `skillsPage` out (the org's skills and the global catalog, #2155).
 * The server filters, searches, sorts and counts. The data half of
 * SkillsListScreen.
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

  const { data, previousData, loading, error, refetch } = useQuery<SkillsData>(SKILLS_LIST_PAGE, {
    variables: {
      orgId,
      ...skillsPageVariables({
        q: state.q,
        filters: list.filters,
        sort: state.sort,
        page: state.page,
        pageSize: state.pageSize,
      }),
    },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });
  const shown = data ?? previousData;
  const rows = shown?.skillsPage.items ?? [];
  const totalCount = shown?.skillsPage.totalCount ?? rows.length;

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
    loading: (loading || orgLoading || !orgId) && !shown && !error,
    // Rows on screen answer the previous list state while the next loads.
    stale: loading && !data && Boolean(shown),
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    importOpen: params?.get(IMPORT_PARAM) === "1",
    onImportOpenChange: setImportOpen,
  };
}

export type SkillsListState = ReturnType<typeof useSkillsList>;
