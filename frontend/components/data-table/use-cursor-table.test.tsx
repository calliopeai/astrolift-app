import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { gql } from "@apollo/client";

import { useCursorTable } from "./use-cursor-table";

/**
 * The cursor walk is the load-bearing half of every migrated surface, so
 * these drive the real hook against a fake Apollo that records the exact
 * variables each render asked for. What matters is not that a request was
 * made but *which* — the failure mode this replaces was a table that
 * fetched 100 rows and paged them locally, which looks fine until row 101.
 */

// The mock resolves from a scripted queue keyed on the variables it is
// handed, so a test can assert the walk asked for the page it claims to be
// showing rather than trusting the rendered output.
const apollo = vi.hoisted(() => ({
  calls: [] as Record<string, unknown>[],
  respond: (_vars: Record<string, unknown>) =>
    ({ items: [], nextCursor: null, totalCount: 0 }) as {
      items: unknown[];
      nextCursor: string | null;
      totalCount: number | null;
    },
  loading: false,
  error: undefined as { message: string } | undefined,
  refetch: vi.fn(),
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: (_doc: unknown, options: { variables: Record<string, unknown>; skip?: boolean }) => {
    apollo.calls.push(options.variables);
    if (options.skip) return { data: undefined, loading: false, refetch: apollo.refetch };
    return {
      data: apollo.error ? undefined : { page: apollo.respond(options.variables) },
      previousData: undefined,
      loading: apollo.loading,
      error: apollo.error,
      refetch: apollo.refetch,
    };
  },
}));

const QUERY = gql`
  query Page($limit: Int, $after: String, $search: String) {
    page(limit: $limit, after: $after, search: $search) {
      items {
        id
      }
      nextCursor
      totalCount
    }
  }
`;

type Row = { id: string };

const extract = (data: unknown) => (data as { page?: { items: Row[] } } | undefined)?.page;

/** A stream of `total` rows served `size` at a time, cursor = next index. */
function pagedSource(total: number, size: number) {
  return (vars: Record<string, unknown>) => {
    const start = vars.after ? Number(vars.after) : 0;
    const limit = (vars.limit as number) ?? size;
    const items = Array.from({ length: Math.max(0, Math.min(limit, total - start)) }, (_, i) => ({
      id: `row-${start + i}`,
    }));
    const end = start + items.length;
    return { items, nextCursor: end < total ? String(end) : null, totalCount: total };
  };
}

const lastCall = () => apollo.calls[apollo.calls.length - 1];

beforeEach(() => {
  apollo.calls = [];
  apollo.loading = false;
  apollo.error = undefined;
  apollo.refetch = vi.fn().mockResolvedValue({});
  apollo.respond = () => ({ items: [], nextCursor: null, totalCount: 0 });
});

describe("useCursorTable", () => {
  it("asks for the page size it was given and no cursor on first load", () => {
    apollo.respond = pagedSource(10, 3);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, pageSize: 3 })
    );

    expect(lastCall()).toMatchObject({ limit: 3 });
    expect(lastCall().after).toBeUndefined();
    expect(result.current.rows).toHaveLength(3);
    expect(result.current.pageIndex).toBe(0);
    expect(result.current.hasPrev).toBe(false);
    expect(result.current.hasNext).toBe(true);
  });

  it("walks forward by sending the server's own cursor back", () => {
    apollo.respond = pagedSource(7, 3);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, pageSize: 3 })
    );

    act(() => result.current.next());
    expect(lastCall()).toMatchObject({ after: "3", limit: 3 });
    expect(result.current.rows.map((r) => r.id)).toEqual(["row-3", "row-4", "row-5"]);
    expect(result.current.pageIndex).toBe(1);

    act(() => result.current.next());
    expect(result.current.rows.map((r) => r.id)).toEqual(["row-6"]);
    expect(result.current.hasNext).toBe(false);
  });

  it("goes back by popping the cursor stack, not by seeking backwards", () => {
    // Keyset pagination has no "before this row" without a reversed query,
    // so prev must replay the cursor that produced the earlier page.
    apollo.respond = pagedSource(9, 3);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, pageSize: 3 })
    );

    act(() => result.current.next());
    act(() => result.current.next());
    expect(result.current.pageIndex).toBe(2);

    act(() => result.current.prev());
    expect(lastCall()).toMatchObject({ after: "3" });
    expect(result.current.rows.map((r) => r.id)).toEqual(["row-3", "row-4", "row-5"]);

    act(() => result.current.prev());
    expect(lastCall().after).toBeUndefined();
    expect(result.current.rows.map((r) => r.id)).toEqual(["row-0", "row-1", "row-2"]);
    expect(result.current.hasPrev).toBe(false);
  });

  it("never pages past the end", () => {
    apollo.respond = pagedSource(2, 5);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, pageSize: 5 })
    );

    expect(result.current.hasNext).toBe(false);
    act(() => result.current.next());
    expect(result.current.pageIndex).toBe(0);
  });

  it("restarts the walk when the search term changes", async () => {
    // A cursor is a position in a result set. Reusing one after the filter
    // changes seeks into rows that may no longer be there — the page would
    // silently skip or repeat.
    apollo.respond = pagedSource(20, 5);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, pageSize: 5, searchVariable: "search" })
    );

    act(() => result.current.next());
    expect(result.current.pageIndex).toBe(1);

    act(() => result.current.setSearch("prod"));
    await waitFor(() => expect(lastCall().search).toBe("prod"));
    expect(result.current.pageIndex).toBe(0);
    expect(lastCall().after).toBeUndefined();
  });

  it("restarts the walk when the page size changes", () => {
    apollo.respond = pagedSource(20, 5);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, pageSize: 5 })
    );

    act(() => result.current.next());
    act(() => result.current.setPageSize(10));
    expect(result.current.pageIndex).toBe(0);
    expect(lastCall()).toMatchObject({ limit: 10 });
    expect(lastCall().after).toBeUndefined();
  });

  it("debounces search rather than firing per keystroke", async () => {
    apollo.respond = pagedSource(20, 5);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, searchVariable: "search" })
    );

    act(() => result.current.setSearch("p"));
    act(() => result.current.setSearch("pr"));
    act(() => result.current.setSearch("pro"));

    // The typed value is live for the input, but nothing has reached the
    // server yet — /administration/audit issued one query per keystroke.
    expect(result.current.search).toBe("pro");
    expect(result.current.isSearching).toBe(true);
    expect(apollo.calls.every((c) => c.search !== "pro")).toBe(true);

    await waitFor(() => expect(result.current.isSearching).toBe(false));
    expect(lastCall().search).toBe("pro");
  });

  it("sends null, not an empty string, when the search box is cleared", async () => {
    apollo.respond = pagedSource(5, 5);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, searchVariable: "search" })
    );
    expect(lastCall().search).toBeNull();

    act(() => result.current.setSearch("x"));
    await waitFor(() => expect(lastCall().search).toBe("x"));
    act(() => result.current.setSearch(""));
    await waitFor(() => expect(lastCall().search).toBeNull());
  });

  it("omits the search variable entirely when the query has no search arg", () => {
    apollo.respond = pagedSource(5, 5);
    const { result } = renderHook(() => useCursorTable<Row>({ query: QUERY, extract }));

    expect(result.current.searchEnabled).toBe(false);
    expect(lastCall()).not.toHaveProperty("search");
  });

  it("reports totalCount from the server, spanning every page", () => {
    apollo.respond = pagedSource(137, 25);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, pageSize: 25 })
    );

    expect(result.current.rows).toHaveLength(25);
    expect(result.current.totalCount).toBe(137);
  });

  describe("page state", () => {
    it("is loading before the first page arrives", () => {
      apollo.loading = true;
      apollo.respond = () => undefined as never;
      const { result } = renderHook(() => useCursorTable<Row>({ query: QUERY, extract }));
      expect(result.current.state).toBe("loading");
    });

    it("is empty when the list is genuinely empty", () => {
      apollo.respond = pagedSource(0, 25);
      const { result } = renderHook(() => useCursorTable<Row>({ query: QUERY, extract }));
      expect(result.current.state).toBe("empty");
    });

    it("is emptyFiltered when a search excluded everything", async () => {
      // Distinct from `empty` on purpose: telling a first-run operator
      // "no results for 'x'" and telling a searching one "you have none
      // yet" are both wrong, and both were shipping.
      apollo.respond = pagedSource(0, 25);
      const { result } = renderHook(() =>
        useCursorTable<Row>({ query: QUERY, extract, searchVariable: "search" })
      );

      act(() => result.current.setSearch("nothing-matches"));
      await waitFor(() => expect(result.current.state).toBe("emptyFiltered"));
    });

    it("is error when the query failed, and retry refetches", () => {
      apollo.error = { message: "boom" };
      const { result } = renderHook(() => useCursorTable<Row>({ query: QUERY, extract }));

      expect(result.current.state).toBe("error");
      expect(result.current.error?.message).toBe("boom");

      act(() => result.current.retry());
      expect(apollo.refetch).toHaveBeenCalled();
    });

    it("is ready once rows arrive", () => {
      apollo.respond = pagedSource(3, 25);
      const { result } = renderHook(() => useCursorTable<Row>({ query: QUERY, extract }));
      expect(result.current.state).toBe("ready");
    });
  });

  describe("sorting", () => {
    it("is inert unless the query exposes a sort argument", () => {
      apollo.respond = pagedSource(5, 5);
      const { result } = renderHook(() => useCursorTable<Row>({ query: QUERY, extract }));
      expect(result.current.sortEnabled).toBe(false);
      expect(lastCall()).not.toHaveProperty("sortBy");
    });

    it("cycles asc → desc → off and restarts the walk", () => {
      apollo.respond = pagedSource(20, 5);
      const { result } = renderHook(() =>
        useCursorTable<Row>({ query: QUERY, extract, pageSize: 5, sortVariable: "sortBy" })
      );

      act(() => result.current.next());
      act(() => result.current.toggleSort("name"));
      expect(result.current.sort).toEqual({ key: "name", dir: "asc" });
      expect(lastCall().sortBy).toBe("name:asc");
      // Sort order change invalidates every cursor issued under the old one.
      expect(result.current.pageIndex).toBe(0);

      act(() => result.current.toggleSort("name"));
      expect(lastCall().sortBy).toBe("name:desc");

      act(() => result.current.toggleSort("name"));
      expect(result.current.sort).toBeUndefined();
      expect(lastCall()).not.toHaveProperty("sortBy");
    });
  });

  it("skips the query when told to", () => {
    apollo.respond = pagedSource(5, 5);
    const { result } = renderHook(() =>
      useCursorTable<Row>({ query: QUERY, extract, skip: true })
    );
    expect(result.current.rows).toEqual([]);
  });
});
