import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FEED_NOW, makeEvents } from "./fixtures";
import { dayLabel, groupItems, mayAutoLoad, useLoadMore } from "./use-feed";

const state = (
  itemCount: number,
  over: Partial<{ hasMore: boolean; loadingMore: boolean }> = {}
) => ({
  hasMore: true,
  loadingMore: false,
  itemCount,
  ...over,
});

describe("mayAutoLoad", () => {
  it("asks once per page", () => {
    expect(mayAutoLoad(state(20), null)).toBe(true);
    expect(mayAutoLoad(state(20), 20)).toBe(false);
    expect(mayAutoLoad(state(40), 20)).toBe(true);
  });

  it("never asks with a fetch in flight or at the end of the cursor", () => {
    expect(mayAutoLoad(state(20, { loadingMore: true }), null)).toBe(false);
    expect(mayAutoLoad(state(20, { hasMore: false }), null)).toBe(false);
  });
});

describe("useLoadMore", () => {
  it("calls once when the sentinel fires twice before the page lands", () => {
    const onLoadMore = vi.fn();
    const { result } = renderHook(() => useLoadMore(state(20), onLoadMore));
    result.current.auto();
    result.current.auto();
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("asks again once the page has landed", () => {
    const onLoadMore = vi.fn();
    const { result, rerender } = renderHook(({ s }) => useLoadMore(s, onLoadMore), {
      initialProps: { s: state(20) },
    });
    result.current.auto();
    rerender({ s: state(20, { loadingMore: true }) });
    result.current.auto();
    rerender({ s: state(40) });
    result.current.auto();
    expect(onLoadMore).toHaveBeenCalledTimes(2);
  });

  it("does not loop after a failed page, but the button still retries", () => {
    const onLoadMore = vi.fn();
    const { result, rerender } = renderHook(({ s }) => useLoadMore(s, onLoadMore), {
      initialProps: { s: state(20) },
    });
    result.current.auto();
    rerender({ s: state(20, { loadingMore: true }) });
    rerender({ s: state(20) }); // the fetch failed: same count
    result.current.auto();
    expect(onLoadMore).toHaveBeenCalledTimes(1);
    result.current.manual();
    expect(onLoadMore).toHaveBeenCalledTimes(2);
  });

  it("the button does nothing while a page is in flight or at the end", () => {
    const onLoadMore = vi.fn();
    const { result, rerender } = renderHook(({ s }) => useLoadMore(s, onLoadMore), {
      initialProps: { s: state(20, { loadingMore: true }) },
    });
    result.current.manual();
    rerender({ s: state(20, { hasMore: false }) });
    result.current.manual();
    expect(onLoadMore).not.toHaveBeenCalled();
  });
});

describe("groupItems", () => {
  it("groups consecutive items by a caller key", () => {
    const groups = groupItems(makeEvents(9), { key: (e) => String(e.round) });
    expect(groups.map((g) => [g.label, g.items.length])).toEqual([
      ["1", 4],
      ["2", 4],
      ["3", 1],
    ]);
    expect(new Set(groups.map((g) => g.key)).size).toBe(3);
  });

  it("returns one unlabelled group without a group-by", () => {
    expect(groupItems(makeEvents(3), undefined)).toEqual([
      { key: "all", label: null, items: makeEvents(3) },
    ]);
  });

  it("labels today and yesterday", () => {
    const now = new Date(FEED_NOW);
    expect(dayLabel(now, now)).toBe("Today");
    expect(dayLabel(new Date(FEED_NOW - 24 * 3600_000), now)).toBe("Yesterday");
    expect(dayLabel("not a date", now)).toBe("Unknown date");
  });
});
