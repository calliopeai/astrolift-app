import { describe, expect, it } from "vitest";

import { MEMBERS_LIST, RUNS_LIST } from "./fixtures";
import { countLabel, pageWindow, rangeLabel } from "./paging";
import {
  defaultListState,
  effectiveFilters,
  extractFilterTokens,
  parseListState,
  parseSort,
  serializeListState,
  standardViews,
  toggleSortKey,
} from "./use-list-state";

describe("parseListState / serializeListState", () => {
  it("parses the spec's example URL", () => {
    const s = parseListState(
      RUNS_LIST,
      "view=failed&status=failed&sort=-started,agent&q=7e11&after=abc&pageSize=50"
    );
    expect(s).toEqual({
      view: "failed",
      q: "7e11",
      filters: { status: "failed" },
      sort: [
        { key: "started", dir: "desc" },
        { key: "agent", dir: "asc" },
      ],
      page: 1,
      after: "abc",
      pageSize: 50,
    });
  });

  it("falls back to defaults for unknown or invalid values", () => {
    const s = parseListState(RUNS_LIST, "view=nope&pageSize=7&page=3&unknown=x&sort=");
    expect(s).toEqual(defaultListState(RUNS_LIST));
  });

  it("reads page only for numbered lists and after only for cursor lists", () => {
    expect(parseListState(MEMBERS_LIST, "page=4&after=abc")).toMatchObject({
      page: 4,
      after: null,
    });
    expect(parseListState(RUNS_LIST, "page=4&after=abc")).toMatchObject({ page: 1, after: "abc" });
    expect(parseListState(MEMBERS_LIST, "page=0").page).toBe(1);
    expect(parseListState(MEMBERS_LIST, "page=2.5").page).toBe(1);
  });

  it("leaves defaults out of the URL", () => {
    expect(serializeListState(RUNS_LIST, defaultListState(RUNS_LIST))).toBe("");
  });

  it("round-trips", () => {
    const qs =
      "view=mine&q=hello+world&status=failed&agent=support-bot&sort=status%2C-started&after=c2&pageSize=100";
    const state = parseListState(RUNS_LIST, qs);
    expect(parseListState(RUNS_LIST, serializeListState(RUNS_LIST, state))).toEqual(state);
    expect(serializeListState(RUNS_LIST, state)).toBe(qs);
  });

  it("writes page for numbered lists", () => {
    const state = { ...defaultListState(MEMBERS_LIST), page: 3, filters: { role: "admin" } };
    expect(serializeListState(MEMBERS_LIST, state)).toBe("role=admin&page=3");
  });
});

describe("sort", () => {
  it("parses -key as descending and ignores blanks", () => {
    expect(parseSort("-started,,name,-")).toEqual([
      { key: "started", dir: "desc" },
      { key: "name", dir: "asc" },
    ]);
  });

  it("click sorts by a column, again reverses it, and drops secondary keys", () => {
    const two = [
      { key: "status", dir: "asc" as const },
      { key: "started", dir: "desc" as const },
    ];
    expect(toggleSortKey(two, "agent", false)).toEqual([{ key: "agent", dir: "asc" }]);
    expect(toggleSortKey(two, "status", false)).toEqual([{ key: "status", dir: "desc" }]);
  });

  it("shift-click adds a key, or reverses it in place", () => {
    const one = [{ key: "started", dir: "desc" as const }];
    const added = toggleSortKey(one, "agent", true);
    expect(added).toEqual([
      { key: "started", dir: "desc" },
      { key: "agent", dir: "asc" },
    ]);
    expect(toggleSortKey(added, "started", true)).toEqual([
      { key: "started", dir: "asc" },
      { key: "agent", dir: "asc" },
    ]);
  });
});

describe("extractFilterTokens", () => {
  it("turns known field tokens into filters and keeps the rest as text", () => {
    expect(extractFilterTokens(RUNS_LIST, "status:failed 7e11 agent:support-bot")).toEqual({
      text: "7e11",
      filters: { status: "failed", agent: "support-bot" },
    });
  });

  it("leaves unknown tokens and URLs as text", () => {
    expect(extractFilterTokens(RUNS_LIST, "sha:abc https://example.com/x")).toEqual({
      text: "sha:abc https://example.com/x",
      filters: {},
    });
  });

  it("matches labels case-insensitively and takes quoted values", () => {
    expect(extractFilterTokens(RUNS_LIST, 'STATUS:failed Agent:"support bot"')).toEqual({
      text: "",
      filters: { status: "failed", agent: "support bot" },
    });
  });

  it("while typing, holds back a token at the end of the text", () => {
    expect(extractFilterTokens(RUNS_LIST, "status:fa", { complete: false })).toEqual({
      text: "status:fa",
      filters: {},
    });
    expect(extractFilterTokens(RUNS_LIST, "status:failed ", { complete: false })).toEqual({
      text: "",
      filters: { status: "failed" },
    });
  });

  it("ignores a field with no value", () => {
    expect(extractFilterTokens(RUNS_LIST, "status: x").filters).toEqual({});
  });
});

describe("views", () => {
  it("every list leads with All and Mine", () => {
    const views = standardViews({ owner: "me" }, [{ key: "paused", label: "Paused", filters: {} }]);
    expect(views.map((v) => v.key)).toEqual(["all", "mine", "paused"]);
  });

  it("the person's filters apply over the view's", () => {
    const state = { ...defaultListState(RUNS_LIST), view: "failed", filters: { agent: "x" } };
    expect(effectiveFilters(RUNS_LIST, state)).toEqual({ status: "failed", agent: "x" });
    const mine = { ...defaultListState(RUNS_LIST), view: "mine" };
    expect(effectiveFilters(RUNS_LIST, mine)).toEqual({ startedBy: "me" });
  });
});

describe("paging", () => {
  it("windows page numbers with gaps", () => {
    expect(pageWindow(2, 6)).toEqual([1, 2, 3, "gap", 6]);
    expect(pageWindow(1, 6)).toEqual([1, 2, "gap", 6]);
    expect(pageWindow(1, 1)).toEqual([1]);
    expect(pageWindow(4, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(pageWindow(10, 20)).toEqual([1, "gap", 9, 10, 11, "gap", 20]);
    expect(pageWindow(6, 6)).toEqual([1, "gap", 5, 6]);
  });

  it("labels ranges and counts", () => {
    expect(rangeLabel(1, 25, 140)).toBe("1–25 of 140");
    expect(rangeLabel(6, 25, 140)).toBe("126–140 of 140");
    expect(rangeLabel(1, 25, 0)).toBe("0 of 0");
    expect(countLabel(1240, true)).toBe("about 1.2k");
    expect(countLabel(1000, true)).toBe("about 1k");
    expect(countLabel(48_000, true)).toBe("about 48k");
    expect(countLabel(2_300_000, true)).toBe("about 2.3m");
    expect(countLabel(800, true)).toBe("about 800");
    expect(countLabel(1240, false)).toBe("1,240");
  });
});
