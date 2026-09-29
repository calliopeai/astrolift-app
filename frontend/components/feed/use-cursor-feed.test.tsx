import { act, renderHook, waitFor } from "@testing-library/react";
import { gql } from "@apollo/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCursorFeed } from "./use-cursor-feed";
import { useGrowingLimit } from "./use-growing-limit";

type Item = { id: string };
type Data = { page: { items: Item[]; nextCursor: string | null; totalCount?: number | null } };

const state = vi.hoisted(() => ({
  head: null as null | { items: { id: string }[]; nextCursor: string | null },
  older: {} as Record<string, { items: { id: string }[]; nextCursor: string | null }>,
  headVars: [] as Record<string, unknown>[],
  olderVars: [] as Record<string, unknown>[],
  fail: false,
}));

vi.mock("@apollo/client/react", () => ({
  useApolloClient: () => ({
    query: async ({ variables }: { variables: Record<string, unknown> }) => {
      state.olderVars.push(variables);
      if (state.fail) throw new Error("upstream timed out");
      return { data: { page: state.older[String(variables.after)] } };
    },
  }),
  useQuery: (_doc: unknown, options: { variables: Record<string, unknown>; skip?: boolean }) => {
    state.headVars.push(options.variables);
    const data = options.skip || !state.head ? undefined : { page: state.head };
    return {
      data,
      previousData: undefined,
      loading: false,
      error: undefined,
      refetch: vi.fn(),
    };
  },
}));

const Q = gql`
  query Page($limit: Int, $after: String) {
    page(limit: $limit, after: $after) {
      items {
        id
      }
      nextCursor
    }
  }
`;

const ids = (items: Item[]) => items.map((i) => i.id);

function useFeed(search: string | null = null) {
  return useCursorFeed<Data, Item>(Q, {
    variables: { search },
    select: (d) => d?.page,
    keyOf: (i) => i.id,
    pageSize: 2,
  });
}

describe("useCursorFeed", () => {
  beforeEach(() => {
    state.head = { items: [{ id: "a" }, { id: "b" }], nextCursor: "c1" };
    state.older = {
      c1: { items: [{ id: "c" }, { id: "d" }], nextCursor: "c2" },
      c2: { items: [{ id: "e" }], nextCursor: null },
    };
    state.headVars = [];
    state.olderVars = [];
    state.fail = false;
  });

  it("asks for the newest page without a cursor", () => {
    const { result } = renderHook(() => useFeed());
    expect(ids(result.current.feed.items)).toEqual(["a", "b"]);
    expect(state.headVars.at(-1)).toEqual({ search: null, limit: 2 });
    expect(result.current.feed.hasMore).toBe(true);
  });

  it("appends older pages on the server's cursor until it runs out", async () => {
    const { result } = renderHook(() => useFeed());
    act(() => result.current.feed.onLoadMore());
    await waitFor(() => expect(ids(result.current.feed.items)).toEqual(["a", "b", "c", "d"]));
    expect(state.olderVars.at(-1)).toMatchObject({ after: "c1", limit: 2 });

    act(() => result.current.feed.onLoadMore());
    await waitFor(() => expect(result.current.feed.items).toHaveLength(5));
    expect(result.current.feed.hasMore).toBe(false);
  });

  it("holds new items on the newest page behind the pill", () => {
    const { result, rerender } = renderHook(() => useFeed());
    state.head = { items: [{ id: "z" }, { id: "a" }], nextCursor: "c1" };
    rerender();
    expect(ids(result.current.feed.items)).toEqual(["a"]);
    expect(result.current.feed.newCount).toBe(1);
  });

  it("keeps the items and reports the error when an older page fails", async () => {
    state.fail = true;
    const { result } = renderHook(() => useFeed());
    act(() => result.current.feed.onLoadMore());
    await waitFor(() => expect(result.current.feed.error).toBe("upstream timed out"));
    expect(ids(result.current.feed.items)).toEqual(["a", "b"]);
    expect(result.current.feed.hasMore).toBe(true);
  });

  it("drops the older pages when the question changes", async () => {
    const { result, rerender } = renderHook(({ q }) => useFeed(q), {
      initialProps: { q: null as string | null },
    });
    act(() => result.current.feed.onLoadMore());
    await waitFor(() => expect(result.current.feed.items).toHaveLength(4));
    rerender({ q: "deploy" });
    expect(ids(result.current.feed.items)).toEqual(["a", "b"]);
  });
});

describe("useGrowingLimit", () => {
  it("grows while a full window comes back, and stops at a short one", () => {
    const { result } = renderHook(() => useGrowingLimit(10, 5));
    expect(result.current.feed(10, false).hasMore).toBe(true);
    act(() => result.current.feed(10, false).onLoadMore());
    expect(result.current.limit).toBe(15);
    expect(result.current.feed(12, false).hasMore).toBe(false);
  });
});
