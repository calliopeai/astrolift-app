import { describe, expect, it } from "vitest";

import { matchRows, selectRows, type SelectRowsSpec } from "./select-rows";

interface Row {
  id: string;
  name: string;
  status: "ok" | "failed";
  size: number;
}

const ROWS: Row[] = [
  { id: "a", name: "storefront", status: "ok", size: 3 },
  { id: "b", name: "billing", status: "failed", size: 1 },
  { id: "c", name: "search", status: "ok", size: 2 },
];

const SPEC: SelectRowsSpec<Row> = {
  filter: { status: (r, v) => r.status === v },
  text: (r) => [r.name, r.id],
  sort: { name: (r) => r.name, size: (r) => r.size },
  id: (r) => r.id,
};

const base = {
  filters: {},
  q: "",
  sort: [{ key: "name", dir: "asc" as const }],
  page: 1,
  pageSize: 25,
};

describe("selectRows", () => {
  it("filters, searches and sorts", () => {
    expect(matchRows(ROWS, { ...base, filters: { status: "ok" } }, SPEC).map((r) => r.id)).toEqual([
      "c",
      "a",
    ]);
    expect(matchRows(ROWS, { ...base, q: "BILL" }, SPEC).map((r) => r.id)).toEqual(["b"]);
    expect(
      matchRows(ROWS, { ...base, sort: [{ key: "size", dir: "desc" }] }, SPEC).map((r) => r.id)
    ).toEqual(["a", "c", "b"]);
  });

  it("ignores a filter it has no test for", () => {
    expect(matchRows(ROWS, { ...base, filters: { owner: "me" } }, SPEC)).toHaveLength(3);
  });

  it("pages and counts what matched", () => {
    const { rows, totalCount } = selectRows(ROWS, { ...base, page: 2, pageSize: 2 }, SPEC);
    expect(rows.map((r) => r.id)).toEqual(["a"]);
    expect(totalCount).toBe(3);
  });
});
