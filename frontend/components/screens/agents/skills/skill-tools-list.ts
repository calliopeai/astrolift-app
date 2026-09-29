/**
 * A skill's Tools tab (spec 44 §5.1, embedded): its tool definitions with an
 * adapter filter and numbered pages. `toolDefs(skillId)` returns them all
 * with no filter, sort or page argument, and a skill carries a handful, so
 * `selectSkillTools` answers the rest; the one view says so. A skill's tools
 * have no owner of their own, so there is no Mine. Pure.
 */
import type { SortState } from "@/components/data-table";
import type { ListDefinition } from "@/components/list/use-list-state";

import { clientNote, lower, selectPage, time } from "./catalog";
import { ADAPTERS } from "./tool-adapters";
import type { ToolDef } from "./use-skill-tool-defs";

export const SKILL_TOOLS_LIST: ListDefinition = {
  id: "agents.skill.tools",
  fields: [{ key: "adapter", label: "Adapter", options: ADAPTERS }],
  searchPlaceholder: "Search tools, slugs, handlers…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: [
    {
      key: "all",
      label: "All",
      filters: {},
      note: clientNote("the skill's tools come in one read"),
    },
  ],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function selectSkillTools(
  tools: ToolDef[],
  q: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  return selectPage(
    tools,
    {
      matches: (t, f) => !f.adapter || t.adapter === f.adapter,
      text: (t) => [t.name, t.slug, t.description, t.handlerRef],
      sortValue: {
        name: (t) => lower(t.name),
        adapter: (t) => t.adapter,
        created: (t) => time(t.createdAt),
      },
      id: (t) => t.slug,
    },
    q
  );
}
