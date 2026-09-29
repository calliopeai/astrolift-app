/**
 * Agents › Tools (spec 44 §4.4, §5.1): views All · Mine · Built-in · Custom,
 * an adapter filter, numbered pages.
 *
 * Why the step runs here and not on the server: `orgToolDefs(orgId)` returns
 * every tool on the org's skills and the global catalog at once (capped at
 * 500), with no search, filter, sort or page argument. A tool records no
 * owner, and its `is_builtin` flag is not in the API yet, so Mine lists every
 * tool and Built-in and Custom cannot split them. Each view's note says so.
 * Pure.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import {
  clientNote,
  lower,
  selectPage,
  time,
  withAllNote,
} from "@/components/screens/agents/skills/catalog";
import { ADAPTERS } from "@/components/screens/agents/skills/tool-adapters";

import type { ToolRegistryTool } from "./use-tool-registry";

const NOTE = clientNote("the registry returns every tool at once, up to 500");
const NO_FLAG =
  "Tools do not carry their built-in flag in the API yet, so this view cannot pick them out and stays empty. Every tool is in All.";

export const TOOLS_LIST: ListDefinition = {
  id: "agents.tools",
  fields: [{ key: "adapter", label: "Adapter", options: ADAPTERS }],
  searchPlaceholder: "Search tools, slugs, handlers…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: withAllNote(
    standardViews(
      {},
      [
        { key: "builtin", label: "Built-in", filters: { builtin: "1" }, note: NO_FLAG },
        { key: "custom", label: "Custom", filters: { builtin: "0" }, note: NO_FLAG },
      ],
      {
        mineNote: `Mine lists every tool until tools record who registered them. ${NOTE}`,
      }
    ),
    NOTE
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function selectTools(
  tools: ToolRegistryTool[],
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
      matches: (t, f) => {
        // No tool carries is_builtin yet (see the views' note).
        if (f.builtin) return false;
        return !f.adapter || t.adapter === f.adapter;
      },
      text: (t) => [t.name, t.slug, t.description, t.handlerRef],
      sortValue: {
        name: (t) => lower(t.name),
        adapter: (t) => t.adapter,
        created: (t) => time(t.createdAt),
      },
      id: (t) => t.id,
    },
    q
  );
}
