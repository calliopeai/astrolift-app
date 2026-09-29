import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { ALL_ENVIRONMENTS } from "./environments.fixtures";
import { environmentKind, ENVIRONMENTS_LIST, selectEnvironments } from "./environments-list";

function namesFor(qs: string) {
  const state = parseListState(ENVIRONMENTS_LIST, qs);
  return selectEnvironments(ALL_ENVIRONMENTS, {
    filters: effectiveFilters(ENVIRONMENTS_LIST, state),
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  }).rows.map((e) => `${e.registeredAppSlug}/${e.name}`);
}

describe("environmentKind", () => {
  it("reads production and previews from the name", () => {
    expect(environmentKind("prod")).toBe("production");
    expect(environmentKind("Production")).toBe("production");
    expect(environmentKind("preview-pr-412")).toBe("preview");
    expect(environmentKind("pr-88")).toBe("preview");
    expect(environmentKind("staging")).toBe("other");
    expect(environmentKind("product-demo")).toBe("other");
  });
});

describe("selectEnvironments", () => {
  it("leads with All and Mine, then Production and Previews", () => {
    expect(ENVIRONMENTS_LIST.views.map((v) => v.label)).toEqual([
      "All",
      "Mine",
      "Production",
      "Previews",
    ]);
  });

  it("sorts by app, then name", () => {
    expect(namesFor("")).toEqual([
      "billing-api/dev",
      "storefront/preview-pr-412",
      "storefront/prod",
      "storefront/staging",
    ]);
  });

  it("Production and Previews go by the name", () => {
    expect(namesFor("view=production")).toEqual(["storefront/prod"]);
    expect(namesFor("view=previews")).toEqual(["storefront/preview-pr-412"]);
  });

  it("Mine holds nothing rather than everything", () => {
    expect(namesFor("view=mine")).toEqual([]);
  });

  it("the deploys chip keeps paused or active", () => {
    expect(namesFor("deploys=paused")).toEqual(["storefront/staging"]);
  });
});
