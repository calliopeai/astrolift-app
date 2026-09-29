/**
 * List state (spec 44 §5.1): view, search, filters, sort and page, all in the
 * URL, so any list state can be linked and survives a reload.
 *
 *   ?view=failed&q=7e11&status=failed&agent=support-bot&sort=-started,name&after=<cursor>&pageSize=50
 *
 * This is the pure half, safe on the server: the definitions, parse and
 * serialize. The hooks that bind it to the URL or to memory live in
 * use-list-state.ts, a client module. The parse/serialize pair is pure and unit-tested; the two hooks only bind
 * it to a store: `useListState` to the URL (every routed list), and
 * `useLocalListState` to memory (stories, and lists embedded in a panel that
 * must not own the page's query string).
 *
 * Column visibility and list-or-cards are per person per list, so they live
 * in localStorage, not in a link someone else opens.
 */

import type { SortDirection, SortState } from "@/components/data-table";

// ---------------------------------------------------------------------------
// Declaration
// ---------------------------------------------------------------------------

export interface ListFieldOption {
  value: string;
  label: string;
}

/** A filterable field. Its key is the URL param and the search token. */
export interface ListField {
  key: string;
  label: string;
  /** A fixed set of values (status, kind). */
  options?: ListFieldOption[];
  /** Values looked up as the person types (agent, project). */
  async?: (query: string) => Promise<ListFieldOption[]>;
}

/** A view is a saved filter, shown as a tab (spec 44 §4.4). */
export interface ListView {
  key: string;
  label: string;
  /** Applied under the person's own filters; not shown as chips. */
  filters: Record<string, string>;
  /**
   * Said under the tabs while the view is active, when it means less than
   * its name: a Mine the backend cannot fully answer yet says what it covers.
   */
  note?: string;
}

export type ListPaging = "numbered" | "cursor";

export interface ListDefinition {
  /** Stable id: the localStorage namespace for this list's columns and mode. */
  id: string;
  fields: ListField[];
  searchPlaceholder: string;
  /** Usually newest first: `[{ key: "started", dir: "desc" }]`. */
  defaultSort: SortState[];
  /** First is the default. Build with `standardViews` so All and Mine lead. */
  views: ListView[];
  paging: ListPaging;
  pageSizes: number[];
  /** Defaults to the first of `pageSizes`. */
  defaultPageSize?: number;
}

/**
 * Every list has All and Mine (spec 44 §4.4, decision 18). The caller says
 * what Mine filters on (`{ owner: "me" }`, `{ startedBy: "me" }`).
 */
export function standardViews(
  mine: Record<string, string>,
  extra: ListView[] = [],
  { mineNote }: { mineNote?: string } = {}
): ListView[] {
  return [
    { key: "all", label: "All", filters: {} },
    { key: "mine", label: "Mine", filters: mine, note: mineNote },
    ...extra,
  ];
}

// ---------------------------------------------------------------------------
// State, parse and serialize (pure)
// ---------------------------------------------------------------------------

export interface ListState {
  view: string;
  q: string;
  /** The person's own filters, one value per field; each is a chip. */
  filters: Record<string, string>;
  /** Ordered keys; the first is the primary sort. */
  sort: SortState[];
  /** 1-based; numbered paging only. */
  page: number;
  /** Cursor paging only; null is the first (newest) page. */
  after: string | null;
  pageSize: number;
}

/** Param names the list itself owns; a field must not be called one of these. */
export const RESERVED_PARAMS = ["view", "q", "sort", "page", "after", "pageSize"] as const;

function defaultPageSize(def: ListDefinition): number {
  return def.defaultPageSize ?? def.pageSizes[0] ?? 25;
}

export function defaultListState(def: ListDefinition): ListState {
  return {
    view: def.views[0]?.key ?? "all",
    q: "",
    filters: {},
    sort: def.defaultSort,
    page: 1,
    after: null,
    pageSize: defaultPageSize(def),
  };
}

export function formatSort(sort: SortState[]): string {
  return sort.map((s) => (s.dir === "desc" ? `-${s.key}` : s.key)).join(",");
}

export function parseSort(raw: string): SortState[] {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part && part !== "-")
    .map((part) =>
      part.startsWith("-")
        ? { key: part.slice(1), dir: "desc" as SortDirection }
        : { key: part, dir: "asc" as SortDirection }
    );
}

export function parseListState(def: ListDefinition, params: URLSearchParams | string): ListState {
  const p = typeof params === "string" ? new URLSearchParams(params) : params;
  const base = defaultListState(def);

  const view = p.get("view");
  const filters: Record<string, string> = {};
  for (const field of def.fields) {
    const value = p.get(field.key);
    if (value) filters[field.key] = value;
  }
  const sort = parseSort(p.get("sort") ?? "");
  const page = Number(p.get("page"));
  const pageSize = Number(p.get("pageSize"));

  return {
    view: view && def.views.some((v) => v.key === view) ? view : base.view,
    q: p.get("q") ?? "",
    filters,
    sort: sort.length > 0 ? sort : base.sort,
    page: def.paging === "numbered" && Number.isInteger(page) && page > 1 ? page : 1,
    after: def.paging === "cursor" ? p.get("after") || null : null,
    pageSize: def.pageSizes.includes(pageSize) ? pageSize : base.pageSize,
  };
}

/** The query string for a state, without `?`; defaults are left out. */
export function serializeListState(def: ListDefinition, state: ListState): string {
  const base = defaultListState(def);
  const p = new URLSearchParams();
  if (state.view !== base.view) p.set("view", state.view);
  if (state.q) p.set("q", state.q);
  for (const field of def.fields) {
    const value = state.filters[field.key];
    if (value) p.set(field.key, value);
  }
  const sort = formatSort(state.sort);
  if (sort && sort !== formatSort(base.sort)) p.set("sort", sort);
  if (def.paging === "numbered" && state.page > 1) p.set("page", String(state.page));
  if (def.paging === "cursor" && state.after) p.set("after", state.after);
  if (state.pageSize !== base.pageSize) p.set("pageSize", String(state.pageSize));
  return p.toString();
}

/** The view's filters under the person's own: what the query is sent. */
/**
 * A list's query merged into the page's own: params the list does not own
 * (a host tab's `?section=`, `?kind=`) survive every list change.
 */
export function mergeListQuery(def: ListDefinition, current: string, listQuery: string): string {
  const owned = new Set([
    "view",
    "q",
    "sort",
    "page",
    "after",
    "pageSize",
    ...def.fields.map((f) => f.key),
  ]);
  const p = new URLSearchParams();
  for (const [key, value] of new URLSearchParams(current))
    if (!owned.has(key)) p.append(key, value);
  for (const [key, value] of new URLSearchParams(listQuery)) p.append(key, value);
  return p.toString();
}

export function effectiveFilters(def: ListDefinition, state: ListState): Record<string, string> {
  const view = def.views.find((v) => v.key === state.view);
  return { ...view?.filters, ...state.filters };
}

/**
 * Header click (spec 44 §5.1): a click sorts by that column, or reverses it
 * when it is already the primary key; shift-click adds it as the next key,
 * or reverses it where it already is. There is no unsorted state: a list
 * always has an order, and the default is the way back to it.
 */
export function toggleSortKey(sort: SortState[], key: string, additive: boolean): SortState[] {
  const flip = (s: SortState): SortState => ({ key: s.key, dir: s.dir === "asc" ? "desc" : "asc" });
  if (!additive) {
    const primary = sort[0];
    return [primary?.key === key ? flip(primary) : { key, dir: "asc" }];
  }
  return sort.some((s) => s.key === key)
    ? sort.map((s) => (s.key === key ? flip(s) : s))
    : [...sort, { key, dir: "asc" }];
}

// ---------------------------------------------------------------------------
// Filter tokens in search
// ---------------------------------------------------------------------------

const TOKEN = /(^|\s)([A-Za-z][\w-]*):("([^"]*)"|(\S+))(?=\s|$)/g;

/**
 * Pull `field:value` tokens out of the search text (spec 44 §5.1), so typing
 * `status:failed` makes the same chip as `+ Filter`. Only the list's own
 * fields are tokens, matched on key or label, case-insensitive; anything else
 * (`http://…`, `sha:abc` on a list with no sha field) stays text. Quote a
 * value with spaces: `agent:"support bot"`.
 *
 * With `complete: false` (while typing), a token at the very end of the text
 * is left alone, because the person may not have finished typing its value.
 */
export function extractFilterTokens(
  def: ListDefinition,
  text: string,
  { complete = true }: { complete?: boolean } = {}
): { text: string; filters: Record<string, string> } {
  const filters: Record<string, string> = {};
  const byName = new Map<string, string>();
  for (const f of def.fields) {
    byName.set(f.key.toLowerCase(), f.key);
    byName.set(f.label.toLowerCase(), f.key);
  }
  const rest = text.replace(
    TOKEN,
    (match, lead: string, name: string, _v, quoted?: string, bare?: string, offset?: number) => {
      const key = byName.get(name.toLowerCase());
      const value = quoted ?? bare ?? "";
      const atEnd = (offset ?? 0) + match.length === text.length;
      if (!key || !value || (!complete && atEnd)) return match;
      filters[key] = value;
      return lead;
    }
  );
  return { text: rest.replace(/\s+/g, " ").trim(), filters };
}

// ---------------------------------------------------------------------------
// Per-person preferences (localStorage)
// ---------------------------------------------------------------------------

export type ListMode = "list" | "card";

export interface ListStateController {
  definition: ListDefinition;
  state: ListState;
  /** View filters plus the person's filters: send these to the query. */
  filters: Record<string, string>;
  /** True when search or a chip is narrowing the view. */
  isFiltered: boolean;
  setSearch: (q: string) => void;
  setFilter: (key: string, value: string | null) => void;
  /** Search text and chips together, from one typed search. */
  applySearch: (q: string, filters: Record<string, string>) => void;
  clearFilters: () => void;
  toggleSort: (key: string, additive: boolean) => void;
  setSort: (sort: SortState[]) => void;
  setPage: (page: number) => void;
  /** Cursor paging: go to the page after `nextCursor`. */
  older: (nextCursor: string) => void;
  /** Cursor paging: back one page (to the newest after a reload). */
  newer: () => void;
  hasNewer: boolean;
  setPageSize: (size: number) => void;
  /** Href for a view tab (spec 44 §4.4 rule 5). */
  viewHref: (view: string) => string;
  hiddenColumns: string[];
  toggleColumn: (id: string) => void;
  mode: ListMode;
  setMode: (mode: ListMode) => void;
}
