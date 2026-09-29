import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { APPS_LIST, appsCrumbs, appStatusKey, appsPageVariables, pinFirst } from "./apps-list";
import { APPS } from "./fixtures";

function varsFor(qs: string) {
  const state = parseListState(APPS_LIST, qs);
  return appsPageVariables({
    q: state.q,
    filters: effectiveFilters(APPS_LIST, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
}

describe("APPS_LIST", () => {
  it("leads with All and Mine, then Failing and Archived, on numbered pages", () => {
    expect(APPS_LIST.views.map((v) => v.label)).toEqual(["All", "Mine", "Failing", "Archived"]);
    expect(APPS_LIST.paging).toBe("numbered");
    expect(APPS_LIST.views.find((v) => v.key === "mine")?.note).toMatch(/until apps record/);
  });

  it("filters on project, status, last deploy, kind and cluster", () => {
    expect(APPS_LIST.fields.map((f) => f.key)).toEqual([
      "project",
      "status",
      "deploy",
      "kind",
      "cluster",
    ]);
  });
});

describe("appsCrumbs", () => {
  it("is one Apps switcher over the area's functions", () => {
    const [apps] = appsCrumbs();
    expect(apps.label).toBe("Apps");
    expect(apps.switcher?.find((o) => o.active)?.href).toBe("/apps");
  });
});

describe("appsPageVariables", () => {
  it("asks for the first numbered page, newest first, with no filter", () => {
    expect(varsFor("")).toEqual({
      includeFreshness: true,
      search: null,
      filter: null,
      sort: "-created",
      page: 1,
      pageSize: 25,
    });
  });

  it("Failing and Archived are server filters", () => {
    expect(varsFor("view=failing").filter).toEqual({ failing: true });
    expect(varsFor("view=archived").filter).toEqual({ archived: true });
  });

  it("status and last deploy go as the schema's upper-case enum members", () => {
    expect(varsFor("status=live&deploy=never").filter).toEqual({
      status: ["LIVE"],
      deploy: ["NEVER"],
    });
    expect(appStatusKey(APPS[0])).toBe("live");
  });

  it("sends project, kind and cluster as one-value lists", () => {
    expect(
      varsFor("project=Web%20properties&kind=service-worker&cluster=stg-us-east-1").filter
    ).toEqual({
      project: ["Web properties"],
      kind: ["service-worker"],
      cluster: ["stg-us-east-1"],
    });
  });

  it("sends the search, the column sort and the page", () => {
    expect(varsFor("q=%20gateway%20&sort=name&page=3&pageSize=50")).toMatchObject({
      search: "gateway",
      sort: "name",
      page: 3,
      pageSize: 50,
    });
  });
});

describe("pinFirst", () => {
  it("lifts pins to the top of the page in hand, keeping the server's order otherwise", () => {
    const rows = [{ slug: "a" }, { slug: "b" }, { slug: "c" }];
    expect(pinFirst(rows, new Set(["c"])).map((r) => r.slug)).toEqual(["c", "a", "b"]);
    expect(pinFirst(rows, new Set()).map((r) => r.slug)).toEqual(["a", "b", "c"]);
  });

  it("never pulls a pin in from another page", () => {
    expect(pinFirst([{ slug: "a" }], new Set(["z"])).map((r) => r.slug)).toEqual(["a"]);
  });
});
