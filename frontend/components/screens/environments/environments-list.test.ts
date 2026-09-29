import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { ENVIRONMENTS_LIST, environmentsVariables } from "./environments-list";

function varsFor(qs: string, appSlug: string | null = null) {
  const state = parseListState(ENVIRONMENTS_LIST, qs);
  return environmentsVariables(appSlug, {
    q: state.q,
    filters: effectiveFilters(ENVIRONMENTS_LIST, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
}

describe("ENVIRONMENTS_LIST", () => {
  it("leads with All and Mine, then Production and Previews, with no stand-in notes", () => {
    expect(ENVIRONMENTS_LIST.views.map((v) => v.label)).toEqual([
      "All",
      "Mine",
      "Production",
      "Previews",
    ]);
    expect(ENVIRONMENTS_LIST.views.every((v) => !v.note)).toBe(true);
  });
});

describe("environmentsVariables", () => {
  it("asks for the first numbered page, by app then name, with no filter", () => {
    expect(varsFor("")).toEqual({
      appSlug: null,
      search: null,
      filter: null,
      sort: "app,name",
      page: 1,
      pageSize: 25,
    });
  });

  it("Production and Previews filter on the recorded kind, Mine on the owner", () => {
    expect(varsFor("view=production").filter).toEqual({ kind: ["production"] });
    expect(varsFor("view=previews").filter).toEqual({ kind: ["preview"] });
    expect(varsFor("view=mine").filter).toEqual({ owner: ["me"] });
  });

  it("sends the app and cluster chips, search, sort and page", () => {
    expect(
      varsFor("app=storefront&cluster=prod-west&q=%20stag%20&sort=-cluster&page=2", "storefront")
    ).toEqual({
      appSlug: "storefront",
      search: "stag",
      filter: { app: ["storefront"], cluster: ["prod-west"] },
      sort: "-cluster",
      page: 2,
      pageSize: 25,
    });
  });
});
