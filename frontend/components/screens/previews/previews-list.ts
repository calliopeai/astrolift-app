/**
 * Apps › Previews (spec 44 §4.4, §5.1): the list declaration and the
 * variables it sends. `astroliftPreviewEnvironmentsPage` takes `appSlug`,
 * `search`, `limit` and `after` in the query document this page uses, so
 * app and search go to the server and the status chip narrows a wider page.
 * Previews record who pinned them but not who opened the pull request, so
 * Mine is empty until they do.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type {
  AstroliftPreviewEnvironment,
  PreviewStatus,
} from "@/graphql/lifecycle/lifecycle.types";

const STATUSES: PreviewStatus[] = ["building", "running", "failed", "torn_down"];

/** The page walked when the status chip narrows it client-side. */
export const NARROW_LIMIT = 100;

export const PREVIEWS_LIST: ListDefinition = {
  id: "apps.previews",
  fields: [
    { key: "app", label: "App" },
    {
      key: "status",
      label: "Status",
      options: STATUSES.map((s) => ({ value: s, label: s.replace(/_/g, " ") })),
    },
  ],
  // The server matches app, branch, hostname, commit and status.
  searchPlaceholder: "Search apps, branches, hosts, commits…",
  defaultSort: [{ key: "lastDeploy", dir: "desc" }],
  views: standardViews({ openedBy: "me" }, [], {
    mineNote: "Previews do not record who opened their pull request yet, so Mine is empty.",
  }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export function previewsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    appSlug: filters.app || null,
    search: q.trim() || null,
    limit: filters.status ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

export function narrowPreviews(
  rows: AstroliftPreviewEnvironment[],
  filters: Record<string, string>
): AstroliftPreviewEnvironment[] {
  if (filters.openedBy) return [];
  return filters.status ? rows.filter((p) => p.status === filters.status) : rows;
}

export const PREVIEW_DOT: Record<PreviewStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  building: "pending",
  running: "ok",
  failed: "error",
  torn_down: "muted",
};
