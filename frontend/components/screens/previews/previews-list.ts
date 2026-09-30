/**
 * Apps › Previews (spec 44 §4.4, §5.1): the list declaration and the
 * variables it sends. `astroliftPreviewEnvironmentsPage` answers all of it
 * (#2155): app and search as its own arguments, the status chip and Mine
 * (who opened it) through its `filter`, the order (last deploy, newest
 * first) as `sort`.
 */
import type { SortState } from "@/components/data-table";
import { formatSort, type ListDefinition, standardViews } from "@/components/list/list-state";
import type { PreviewStatus } from "@/graphql/lifecycle/lifecycle.types";

const STATUSES: PreviewStatus[] = ["building", "running", "failed", "torn_down"];

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
  // The server's `deployed`: the last deploy, else when the preview was created.
  defaultSort: [{ key: "deployed", dir: "desc" }],
  views: standardViews({ openedBy: "me" }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** Translate labels while keeping status values, Mine ownership and cursors stable. */
export function localizedPreviewsList(t: (key: string) => string): ListDefinition {
  return {
    ...PREVIEWS_LIST,
    searchPlaceholder: t("searchPlaceholder"),
    fields: PREVIEWS_LIST.fields.map((field) => ({
      ...field,
      label: t(`columns.${field.key}`),
      options: field.options?.map((option) => ({ ...option, label: t(`status.${option.value}`) })),
    })),
    views: PREVIEWS_LIST.views.map((view) => ({ ...view, label: t(`views.${view.key}`) })),
  };
}

export function previewsVariables(
  filters: Record<string, string>,
  {
    q,
    sort,
    pageSize,
    after,
  }: { q: string; sort: SortState[]; pageSize: number; after: string | null }
) {
  const filter: { status?: string[]; openedBy?: string[] } = {};
  if (filters.status) filter.status = [filters.status];
  if (filters.openedBy) filter.openedBy = [filters.openedBy];
  return {
    appSlug: filters.app || null,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    sort: formatSort(sort),
    limit: pageSize,
    after,
  };
}

export const PREVIEW_DOT: Record<PreviewStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  building: "pending",
  running: "ok",
  failed: "error",
  torn_down: "muted",
};
