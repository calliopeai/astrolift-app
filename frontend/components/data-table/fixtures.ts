/**
 * Story fixtures for the data table: a controller in any state and sample
 * rows. Typed against the real controller, so a story cannot drift from what
 * useCursorTable returns.
 */
import type { CursorTableController } from "./use-cursor-table";
import type { PageState } from "./types";

const noop = () => {};

export function fakeController<TRow>(
  overrides: Partial<CursorTableController<TRow>> = {}
): CursorTableController<TRow> {
  return {
    rows: [],
    state: "ready" as PageState,
    error: undefined,
    retry: noop,
    refetch: noop,
    totalCount: null,
    pageIndex: 0,
    hasNext: false,
    hasPrev: false,
    after: null,
    nextCursor: overrides.hasNext ? "fixture-next" : null,
    next: noop,
    prev: noop,
    pageSize: 25,
    setPageSize: noop,
    search: "",
    setSearch: noop,
    isStale: false,
    isSearching: false,
    searchEnabled: true,
    sort: { key: "started", dir: "desc" },
    toggleSort: noop,
    sortEnabled: true,
    isFiltered: false,
    clearFilters: noop,
    ...overrides,
  } as CursorTableController<TRow>;
}

export type RunRow = {
  id: string;
  agent: string;
  status: "running" | "succeeded" | "failed";
  took: string;
  trigger: string;
  started: string;
};

export const RUNS: RunRow[] = [
  {
    id: "7f3c2a91",
    agent: "support-bot",
    status: "running",
    took: "0:42",
    trigger: "webhook",
    started: "2m ago",
  },
  {
    id: "7e11b0c4",
    agent: "support-bot",
    status: "failed",
    took: "1:10",
    trigger: "webhook",
    started: "9m ago",
  },
  {
    id: "7d02e5f7",
    agent: "nightly-sync",
    status: "succeeded",
    took: "4:10",
    trigger: "schedule",
    started: "1h ago",
  },
  {
    id: "7c9a1d33",
    agent: "triage-agent",
    status: "succeeded",
    took: "0:18",
    trigger: "manual",
    started: "3h ago",
  },
];
