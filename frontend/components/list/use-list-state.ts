"use client";

/** The hooks half of list state; the definitions and pure helpers are in list-state.ts. */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import type { ListDefinition, ListState, ListMode, ListStateController } from "./list-state";
import {
  defaultListState,
  parseListState,
  serializeListState,
  mergeListQuery,
  effectiveFilters,
  toggleSortKey,
} from "./list-state";

export * from "./list-state";
const PREF_PREFIX = "astrolift.list.";

function readPref<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(PREF_PREFIX + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function writePref(key: string, value: unknown) {
  try {
    window.localStorage.setItem(PREF_PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage blocked or full: the preference lasts for this visit only.
  }
}

function usePref<T>(key: string, fallback: T): [T, (next: T) => void] {
  const [value, setValue] = React.useState<T>(fallback);
  // Read after mount, never during render, so the server and the first
  // client render agree.
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage is the external system; reading it in render would break hydration
    setValue(readPref(key, fallback));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const set = React.useCallback(
    (next: T) => {
      setValue(next);
      writePref(key, next);
    },
    [key]
  );
  return [value, set];
}

// ---------------------------------------------------------------------------
// The controller every list component takes
// ---------------------------------------------------------------------------

/** Anything that changes the question sends the list back to its first page. */
function firstPage(state: ListState): ListState {
  return { ...state, page: 1, after: null };
}

function useController(
  def: ListDefinition,
  state: ListState,
  commit: (next: ListState) => void,
  href: (qs: string) => string
): ListStateController {
  const [hiddenColumns, setHidden] = usePref<string[]>(`${def.id}.columns`, []);
  const [mode, setMode] = usePref<ListMode>(`${def.id}.mode`, "list");
  // The cursors behind the one in the URL. Memory only: a cursor from a
  // link is a position, and "newer than it" needs the backend's before-cursor.
  const [cursorStack, setCursorStack] = React.useState<(string | null)[]>([]);

  const update = (patch: Partial<ListState>, keepPage = false) => {
    const next = { ...state, ...patch };
    if (!keepPage) setCursorStack([]);
    commit(keepPage ? next : firstPage(next));
  };

  return {
    definition: def,
    state,
    filters: effectiveFilters(def, state),
    isFiltered: state.q.trim() !== "" || Object.keys(state.filters).length > 0,
    setSearch: (q) => update({ q }),
    setFilter: (key, value) => {
      const filters = { ...state.filters };
      if (value) filters[key] = value;
      else delete filters[key];
      update({ filters });
    },
    applySearch: (q, filters) => update({ q, filters: { ...state.filters, ...filters } }),
    clearFilters: () => update({ q: "", filters: {} }),
    toggleSort: (key, additive) => update({ sort: toggleSortKey(state.sort, key, additive) }),
    setSort: (sort) => update({ sort }),
    setPage: (page) => update({ page: Math.max(1, page) }, true),
    older: (nextCursor) => {
      setCursorStack((s) => [...s, state.after]);
      update({ after: nextCursor }, true);
    },
    newer: () => {
      const prev = cursorStack.length > 0 ? cursorStack[cursorStack.length - 1] : null;
      setCursorStack((s) => s.slice(0, -1));
      update({ after: prev }, true);
    },
    hasNewer: state.after !== null,
    setPageSize: (pageSize) => update({ pageSize }),
    viewHref: (view) =>
      href(
        serializeListState(def, {
          ...defaultListState(def),
          view,
          sort: state.sort,
          pageSize: state.pageSize,
        })
      ),
    hiddenColumns,
    toggleColumn: (id) =>
      setHidden(
        hiddenColumns.includes(id) ? hiddenColumns.filter((c) => c !== id) : [...hiddenColumns, id]
      ),
    mode,
    setMode,
  };
}

/** List state in the URL. The routed lists use this. */
export function useListState(def: ListDefinition): ListStateController {
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const qs = params?.toString() ?? "";
  const state = React.useMemo(() => parseListState(def, qs), [def, qs]);
  const href = (query: string) => {
    const merged = mergeListQuery(def, qs, query);
    return merged ? `${pathname}?${merged}` : pathname;
  };
  return useController(
    def,
    state,
    (next) => router.replace(href(serializeListState(def, next)), { scroll: false }),
    href
  );
}

/** List state in memory: stories, and a list embedded inside another page. */
export function useLocalListState(
  def: ListDefinition,
  initial: Partial<ListState> = {}
): ListStateController {
  const [state, setState] = React.useState<ListState>(() => ({
    ...defaultListState(def),
    ...initial,
  }));
  return useController(def, state, setState, (query) => (query ? `?${query}` : "?"));
}
