/**
 * The agent's Skills & tools tab as two lists (spec 44 §5.1; Leo's list
 * rules 3 and 4): the skills bound to it, and the tools those skills carry,
 * one per section. Both sort, filter and page.
 *
 * Why here and not on the server: `agent(orgId, slug)` returns every skill
 * binding with its tools in one read, with no filter, sort or paging. An
 * agent binds a handful of skills, so the hook reads the whole set and these
 * answer the rest exactly, with numbered pages; each view says so. Pure.
 */
import type { SortState } from "@/components/data-table";
import type { ListDefinition } from "@/components/list/use-list-state";
import type { AstroliftAgentSkill, AstroliftToolDef } from "@/graphql/agents/agents.types";

const CLIENT_NOTE =
  "Filtered, sorted and paged in the browser: the agent read returns every binding at once.";

// Kept in step with the tool registry and the Build section.
export const ADAPTER_LABELS: Record<string, string> = {
  python_fn: "Python function",
  http_endpoint: "HTTP endpoint",
  mcp_server: "MCP server",
};

export const AGENT_SKILLS_LIST: ListDefinition = {
  id: "agents.detail.skills",
  fields: [
    {
      key: "status",
      label: "Status",
      options: [
        { value: "active", label: "Active" },
        { value: "inactive", label: "Inactive" },
      ],
    },
    {
      key: "scope",
      label: "Scope",
      options: [
        { value: "global", label: "Global" },
        { value: "org", label: "Organization" },
      ],
    },
  ],
  searchPlaceholder: "Search skills, slugs…",
  defaultSort: [{ key: "position", dir: "asc" }],
  // An agent's skills have no owner of their own, so there is no Mine: one
  // view, and the list draws no view picker.
  views: [{ key: "all", label: "All", filters: {}, note: CLIENT_NOTE }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** One tool, with the skill that carries it. */
export interface AgentToolRow {
  tool: AstroliftToolDef;
  skill: { id: string; name: string; slug: string };
}

export function toolRows(skills: AstroliftAgentSkill[]): AgentToolRow[] {
  return skills.flatMap((b) =>
    b.toolDefs.map((tool) => ({
      tool,
      skill: { id: b.skill.id, name: b.skill.name, slug: b.skill.slug },
    }))
  );
}

export function agentToolsList(adapters: string[]): ListDefinition {
  return {
    id: "agents.detail.tools",
    fields: [
      {
        key: "adapter",
        label: "Adapter",
        options: adapters.map((a) => ({ value: a, label: ADAPTER_LABELS[a] ?? a })),
      },
    ],
    searchPlaceholder: "Search tools, skills, handlers…",
    defaultSort: [{ key: "name", dir: "asc" }],
    views: [{ key: "all", label: "All", filters: {}, note: CLIENT_NOTE }],
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

/** The adapters the agent's tools use, in a stable order, for the filter. */
export function toolAdapters(rows: AgentToolRow[]): string[] {
  return [...new Set(rows.map((r) => r.tool.adapter))].sort();
}

function pageOf<T>(sorted: T[], page: number, pageSize: number): T[] {
  const start = (Math.max(1, page) - 1) * pageSize;
  return sorted.slice(start, start + pageSize);
}

function sortBy<T>(
  rows: T[],
  sort: SortState[],
  compare: (a: T, b: T, key: string) => number,
  tie: (a: T, b: T) => number
): T[] {
  return [...rows].sort((a, b) => {
    for (const s of sort) {
      const c = compare(a, b, s.key);
      if (c !== 0) return s.dir === "asc" ? c : -c;
    }
    return tie(a, b);
  });
}

const has = (needle: string, ...fields: (string | null | undefined)[]) =>
  fields.some((f) => (f ?? "").toLowerCase().includes(needle));

/** Filter, search, sort and page the agent's skills. Stable: ties break on the slug. */
export function selectSkills(
  all: AstroliftAgentSkill[],
  filters: Record<string, string>,
  q: string,
  sort: SortState[],
  page: number,
  pageSize: number
): { rows: AstroliftAgentSkill[]; totalCount: number } {
  const needle = q.trim().toLowerCase();
  const matched = all.filter(({ skill }) => {
    if (filters.status === "active" && !skill.isActive) return false;
    if (filters.status === "inactive" && skill.isActive) return false;
    if (filters.scope === "global" && !skill.isGlobal) return false;
    if (filters.scope === "org" && skill.isGlobal) return false;
    return !needle || has(needle, skill.name, skill.slug, skill.description);
  });
  const sorted = sortBy(
    matched,
    sort,
    (a, b, key) => {
      switch (key) {
        case "name":
          return a.skill.name.localeCompare(b.skill.name);
        case "tools":
          return a.toolDefs.length - b.toolDefs.length;
        case "position":
        default:
          return a.position - b.position;
      }
    },
    (a, b) => a.skill.slug.localeCompare(b.skill.slug)
  );
  return { rows: pageOf(sorted, page, pageSize), totalCount: matched.length };
}

/** Filter, search, sort and page the tools the agent's skills carry. */
export function selectTools(
  all: AgentToolRow[],
  filters: Record<string, string>,
  q: string,
  sort: SortState[],
  page: number,
  pageSize: number
): { rows: AgentToolRow[]; totalCount: number } {
  const needle = q.trim().toLowerCase();
  const matched = all.filter(({ tool, skill }) => {
    if (filters.adapter && tool.adapter !== filters.adapter) return false;
    return (
      !needle ||
      has(needle, tool.name, tool.slug, tool.description, tool.handlerRef, skill.name, skill.slug)
    );
  });
  const sorted = sortBy(
    matched,
    sort,
    (a, b, key) => {
      switch (key) {
        case "skill":
          return a.skill.name.localeCompare(b.skill.name);
        case "adapter":
          return a.tool.adapter.localeCompare(b.tool.adapter);
        case "name":
        default:
          return a.tool.name.localeCompare(b.tool.name);
      }
    },
    (a, b) => a.tool.slug.localeCompare(b.tool.slug) || a.skill.slug.localeCompare(b.skill.slug)
  );
  return { rows: pageOf(sorted, page, pageSize), totalCount: matched.length };
}
