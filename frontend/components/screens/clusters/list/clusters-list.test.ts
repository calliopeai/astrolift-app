import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { CLUSTERS_LIST, clusterCrumbs, clustersPageVariables } from "./clusters-list";

function forQuery(qs: string) {
  const state = parseListState(CLUSTERS_LIST, qs);
  return clustersPageVariables({ ...state, filters: effectiveFilters(CLUSTERS_LIST, state) });
}

describe("CLUSTERS_LIST", () => {
  it("leads with All and Mine, then Offline, on numbered pages", () => {
    expect(CLUSTERS_LIST.views.map((v) => v.key)).toEqual(["all", "mine", "offline"]);
    expect(CLUSTERS_LIST.paging).toBe("numbered");
  });

  it("Mine is who registered the cluster, with no stand-in note", () => {
    const mine = CLUSTERS_LIST.views.find((v) => v.key === "mine");
    expect(mine?.filters).toEqual({ registeredBy: "me" });
    expect(mine?.note).toBeUndefined();
  });
});

describe("clustersPageVariables", () => {
  it("a cold load asks for page 1 by name with no filter", () => {
    expect(forQuery("")).toEqual({
      search: null,
      filter: null,
      sort: "name",
      page: 1,
      pageSize: 25,
    });
  });

  it("the Offline view filters on the heartbeat", () => {
    expect(forQuery("view=offline").filter).toEqual({ live: ["offline"] });
  });

  it("Mine sends me for the server to resolve", () => {
    expect(forQuery("view=mine").filter).toEqual({ registeredBy: ["me"] });
  });

  it("chips combine with the view", () => {
    expect(forQuery("view=offline&provider=eks&status=managing").filter).toEqual({
      provider: ["eks"],
      status: ["managing"],
      live: ["offline"],
    });
  });

  it("sends the search trimmed, the multi-key sort and the page", () => {
    const v = forQuery("q=%20prod%20&sort=provider,-name&page=3&pageSize=50");
    expect(v).toMatchObject({ search: "prod", sort: "provider,-name", page: 3, pageSize: 50 });
  });
});

describe("clusterCrumbs", () => {
  it("starts with an Admin switcher over the Admin functions, Clusters active", () => {
    const [admin, clusters, name] = clusterCrumbs("prd");
    expect(admin.label).toBe("Admin");
    expect(admin.switcher?.find((o) => o.active)?.href).toBe("/clusters");
    expect(admin.switcher?.some((o) => o.href === "/administration/access")).toBe(true);
    expect(clusters).toEqual({ label: "Clusters", href: "/clusters" });
    expect(name).toEqual({ label: "prd" });
  });

  it("ends at Clusters on the list", () => {
    expect(clusterCrumbs().map((c) => c.label)).toEqual(["Admin", "Clusters"]);
  });
});
