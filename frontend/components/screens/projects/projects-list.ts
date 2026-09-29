/**
 * Admin › Projects (spec 44 §4.4, §5.1): the list declaration and the query
 * variables it sends. `astroliftProjectsPage` takes `search`, `limit` and
 * `after`, so search and the cursor go to the server and the order is the
 * server's. Team has no argument yet: it narrows a wider page
 * (`NARROW_LIMIT`) in the client. Projects record no owner, so Mine is empty
 * until they do.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

export const NARROW_LIMIT = 100;

export const PROJECTS_LIST: ListDefinition = {
  id: "admin.projects",
  // `team` is also what the nav tree's "Add project" link sends (?team=<slug>&new=1).
  fields: [{ key: "team", label: "Team" }],
  // The server matches project name, slug and description, plus the owning team.
  searchPlaceholder: "Search projects, slugs, teams…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ owner: "me" }, [], {
    mineNote: "Projects do not record who owns them yet, so Mine is empty.",
  }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export function narrows(filters: Record<string, string>): boolean {
  return Boolean(filters.team || filters.owner);
}

export function projectsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    search: q.trim() || null,
    limit: narrows(filters) ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

export function narrowProjects(
  rows: AstroliftProject[],
  filters: Record<string, string>
): AstroliftProject[] {
  if (filters.owner) return [];
  return filters.team ? rows.filter((p) => p.team.slug === filters.team) : rows;
}
