import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/use-list-state";

import { CLUSTERS_LIST, clusterCrumbs, selectClusters } from "./clusters-list";
import { CLUSTERS, FLEET, ME } from "./fixtures";

const base = { sort: CLUSTERS_LIST.defaultSort, page: 1, pageSize: 25, me: ME };

function forQuery(qs: string) {
  const state = parseListState(CLUSTERS_LIST, qs);
  return selectClusters(CLUSTERS, {
    ...base,
    filters: effectiveFilters(CLUSTERS_LIST, state),
    sort: state.sort,
  });
}

describe("CLUSTERS_LIST", () => {
  it("leads with All and Mine, then Offline, on numbered pages", () => {
    expect(CLUSTERS_LIST.views.map((v) => v.key)).toEqual(["all", "mine", "offline"]);
    expect(CLUSTERS_LIST.paging).toBe("numbered");
  });
});

describe("selectClusters", () => {
  it("sorts by name by default", () => {
    expect(forQuery("").rows.map((c) => c.name)).toEqual([
      "GKE Europe",
      "On-prem lab",
      "Production US West",
      "Staging US East",
    ]);
  });

  it("Offline keeps only clusters whose heartbeat is offline", () => {
    expect(forQuery("view=offline").rows.map((c) => c.slug)).toEqual(["gke-eu-west-4"]);
  });

  it("Mine keeps clusters whose last setup run the viewer started", () => {
    expect(forQuery("view=mine").rows.map((c) => c.slug)).toEqual(["prd-us-west-2"]);
  });

  it("Mine is empty when the viewer is unknown, never everything", () => {
    const state = parseListState(CLUSTERS_LIST, "view=mine");
    const out = selectClusters(CLUSTERS, {
      ...base,
      me: null,
      filters: effectiveFilters(CLUSTERS_LIST, state),
    });
    expect(out.totalCount).toBe(0);
  });

  it("applies provider and status chips together", () => {
    expect(forQuery("provider=eks&status=managing").rows.map((c) => c.slug)).toEqual([
      "stg-us-east-1",
    ]);
  });

  it("sorts by more than one key", () => {
    const rows = forQuery("sort=provider,-name").rows.map((c) => c.slug);
    expect(rows).toEqual(["stg-us-east-1", "prd-us-west-2", "gke-eu-west-4", "onprem-lab"]);
  });

  it("slices a numbered page and counts the whole filtered set", () => {
    const out = selectClusters(FLEET, { ...base, filters: {}, page: 3 });
    expect(out.totalCount).toBe(60);
    expect(out.rows).toHaveLength(10);
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
