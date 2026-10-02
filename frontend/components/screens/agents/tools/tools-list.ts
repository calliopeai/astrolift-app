/**
 * Agents › Tools (spec 44 §4.4, §5.1): views All · Mine · Built-in · Custom,
 * skill and adapter filters, numbered pages.
 *
 * The server answers every view, chip, search, sort and page
 * (`orgToolDefsPage`, #2155): Mine is the tools the viewer registered
 * (`createdBy: "me"`), Built-in and Custom split on the tool's `isBuiltin`
 * flag. Nothing is filtered, sorted or paged in the browser. Pure.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import {
  type NumberedListQuery,
  numberedPageVariables,
} from "@/components/screens/agents/skills/catalog";
import { ADAPTERS } from "@/components/screens/agents/skills/tool-adapters";
import type { Crumb } from "@/components/shell/ShellHeader";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

export const TOOLS_LIST: ListDefinition = {
  id: "agents.tools",
  fields: [
    // Free text: the parent skill's slug.
    { key: "skill", label: "Skill" },
    { key: "adapter", label: "Adapter", options: ADAPTERS },
  ],
  // The server matches name, slug, description, handler and skill slug.
  searchPlaceholder: "Search tools, slugs, handlers…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews(
    { createdBy: "me" },
    [
      { key: "builtin", label: "Built-in", filters: { builtin: "1" } },
      { key: "custom", label: "Custom", filters: { builtin: "0" } },
    ],
    {
      mineNote:
        "Mine means tools you registered. Tools from before Astrolift recorded who registered them are not included in Mine.",
    }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const ADAPTER_VALUES = new Set(ADAPTERS.map((adapter) => adapter.value as string));

export function localizedToolAdapter(t: (key: string) => string, value: string): string {
  return ADAPTER_VALUES.has(value) ? t(value) : value;
}

export function localizedToolsList(t: (key: string) => string): ListDefinition {
  return {
    ...TOOLS_LIST,
    fields: TOOLS_LIST.fields.map((field) => ({
      ...field,
      label: field.key === "skill" || field.key === "adapter" ? t(field.key) : field.label,
      options: field.options?.map((option) => ({
        ...option,
        label: localizedToolAdapter(t, option.value),
      })),
    })),
    searchPlaceholder: t("search"),
    views: TOOLS_LIST.views.map((view) => ({
      ...view,
      label: ["all", "mine", "builtin", "custom"].includes(view.key) ? t(view.key) : view.label,
      note: view.key === "mine" ? t("mineNote") : view.note,
    })),
  };
}

export function localizedToolsCrumbs(t: (key: string) => string): Crumb[] {
  const keys = new Set([
    "agents",
    "fleet",
    "approvals",
    "workflows",
    "runs",
    "functions",
    "skills",
    "tools",
    "environment-specs",
    "models",
    "workloads",
  ]);
  const nav = NAV.map((area) =>
    area.key === "agents"
      ? {
          ...area,
          label: t("agents"),
          groups: area.groups.map((group) => ({
            ...group,
            functions: group.functions.map((item) => ({
              ...item,
              label: keys.has(item.key) ? t(item.key === "tools" ? "title" : item.key) : item.label,
            })),
          })),
        }
      : area
  );
  return [areaSwitcher(nav, "agents", "tools"), { label: t("title") }];
}

export interface ToolDefsFilter {
  skill?: string[];
  adapter?: string[];
  builtin?: boolean;
  createdBy?: string[];
}

/** The list state as `orgToolDefsPage` variables (plus `orgId`, which the hook adds). */
export function toolDefsPageVariables(q: NumberedListQuery) {
  const f = q.filters;
  const filter: ToolDefsFilter = {};
  if (f.skill) filter.skill = [f.skill];
  if (f.adapter) filter.adapter = [f.adapter];
  if (f.builtin) filter.builtin = f.builtin === "1";
  if (f.createdBy) filter.createdBy = [f.createdBy];
  return numberedPageVariables(q, filter, TOOLS_LIST.defaultSort);
}
