/**
 * A skill's Tools tab (spec 44 §5.1, embedded): its tool definitions with an
 * adapter filter and numbered pages. The server answers the chip, search,
 * sort and page (`orgToolDefsPage` narrowed to this skill, #2155). A skill's
 * tools have no owner of their own, so there is no Mine. Pure.
 */
import type { ListDefinition } from "@/components/list/list-state";

import { type NumberedListQuery, numberedPageVariables } from "./catalog";
import { ADAPTERS } from "./tool-adapters";
import type { ToolDef } from "./use-skill-tool-defs";

/** A tool as the tab lists it: no schemas, which only the tool's own page reads. */
export type SkillToolRow = Omit<ToolDef, "inputSchema" | "outputSchema">;

export const SKILL_TOOLS_LIST: ListDefinition = {
  id: "agents.skill.tools",
  fields: [{ key: "adapter", label: "Adapter", options: ADAPTERS }],
  searchPlaceholder: "Search tools, slugs, handlers…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/**
 * The tab's list state as `orgToolDefsPage` variables: this skill by slug,
 * in its own scope, so an org skill and a global one sharing a slug never mix.
 */
export function skillToolsPageVariables(
  skill: { slug: string; isGlobal: boolean },
  q: NumberedListQuery
) {
  const filter: { skill: string[]; scope: string[]; adapter?: string[] } = {
    skill: [skill.slug],
    scope: [skill.isGlobal ? "global" : "org"],
  };
  if (q.filters.adapter) filter.adapter = [q.filters.adapter];
  return numberedPageVariables(q, filter, SKILL_TOOLS_LIST.defaultSort);
}
