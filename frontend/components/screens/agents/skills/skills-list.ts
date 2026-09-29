/**
 * Agents › Skills (spec 44 §4.4, §5.1): views All · Mine · Imported, status
 * and scope filters, numbered pages.
 *
 * Why the step runs here and not on the server: `skills(orgId, isGlobal)`
 * returns the org's skills and the global catalog at once (capped at 200),
 * with no search, filter, sort or page argument, and a skill records neither
 * who wrote it nor the repo it came from. So the hook reads the whole set and
 * `selectSkills` answers the rest; Mine stands in as the organization's own
 * skills, and Imported cannot pick a skill out yet. Each view's note says
 * so. Pure.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/list-state";

import { clientNote, lower, selectPage, time, withAllNote } from "./catalog";

export type SkillListItem = {
  id: string;
  name: string;
  slug: string;
  description: string;
  skillVersion: number;
  isGlobal: boolean;
  isActive: boolean;
  updatedAt?: string | null;
};

const NOTE = clientNote("the registry returns every skill at once, up to 200");

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
  searchPlaceholder: "Search skills, slugs…",
  defaultSort: [{ key: "updated", dir: "desc" }],
  views: withAllNote(
    standardViews(
      { scope: "org" },
      [
        {
          key: "imported",
          label: "Imported",
          filters: { imported: "1" },
          note: "Skills do not record the repo they came from yet, so this view cannot pick them out and stays empty. Imported skills are in All.",
        },
      ],
      {
        mineNote: `Mine means this organization's own skills, not the global catalog, until skills record who wrote them. ${NOTE}`,
      }
    ),
    NOTE
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function selectSkills(
  skills: SkillListItem[],
  q: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  return selectPage(
    skills,
    {
      matches: (s, f) => {
        // No skill carries its source yet (see the Imported view's note).
        if (f.imported) return false;
        if (f.status === "active" && !s.isActive) return false;
        if (f.status === "inactive" && s.isActive) return false;
        if (f.scope === "global" && !s.isGlobal) return false;
        if (f.scope === "org" && s.isGlobal) return false;
        return true;
      },
      text: (s) => [s.name, s.slug, s.description],
      sortValue: {
        name: (s) => lower(s.name),
        version: (s) => s.skillVersion,
        updated: (s) => time(s.updatedAt),
      },
      id: (s) => s.slug,
    },
    q
  );
}
