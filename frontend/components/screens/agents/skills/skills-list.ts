/**
 * Agents › Skills (spec 44 §4.4, §5.1): views All · Mine · Imported, status
 * and scope filters, numbered pages.
 *
 * The server answers every view, chip, search, sort and page (`skillsPage`,
 * #2155): Mine is the skills the viewer wrote (`createdBy: "me"`), Imported
 * is the skills that came from a repo. Nothing is filtered, sorted or paged
 * in the browser. Pure.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";

import { type NumberedListQuery, numberedPageVariables } from "./catalog";

export type SkillListItem = {
  id: string;
  name: string;
  slug: string;
  description: string;
  skillVersion: number;
  isGlobal: boolean;
  isActive: boolean;
  updatedAt?: string | null;
  createdByEmail: string;
  createdByMe: boolean;
  /** repo_import, agent_repo, org_repo, catalogue; empty for a skill written here. */
  sourceKind: string;
  /** The repo (and path) an imported skill came from. */
  sourceRef: string;
  isImported: boolean;
};

export const SKILLS_LIST: ListDefinition = {
  id: "agents.skills",
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
        { value: "org", label: "Organization" },
        { value: "global", label: "Global" },
      ],
    },
  ],
  // The server matches name, slug, description and the import source.
  searchPlaceholder: "Search skills, slugs, repos…",
  defaultSort: [{ key: "updated", dir: "desc" }],
  views: standardViews(
    { createdBy: "me" },
    [{ key: "imported", label: "Imported", filters: { imported: "1" } }],
    {
      mineNote:
        "Mine means skills you wrote or imported. Skills from before Astrolift recorded who wrote them show only in All.",
    }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export interface SkillsFilter {
  scope?: string[];
  active?: boolean;
  imported?: boolean;
  createdBy?: string[];
}

/** The list state as `skillsPage` variables (plus `orgId`, which the hook adds). */
export function skillsPageVariables(q: NumberedListQuery) {
  const f = q.filters;
  const filter: SkillsFilter = {};
  if (f.scope) filter.scope = [f.scope];
  if (f.status) filter.active = f.status === "active";
  if (f.imported) filter.imported = true;
  if (f.createdBy) filter.createdBy = [f.createdBy];
  return numberedPageVariables(q, filter, SKILLS_LIST.defaultSort);
}
