import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { APPS_LIST, appsCrumbs, appStatusKey, selectApps } from "./apps-list";
import { APPS, MANY } from "./fixtures";

function forQuery(qs: string, pinned: ReadonlySet<string> = new Set()) {
  const state = parseListState(APPS_LIST, qs);
  return selectApps(APPS, {
    filters: effectiveFilters(APPS_LIST, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
    pinned,
  });
}

const slugs = (r: { rows: { slug: string }[] }) => r.rows.map((a) => a.slug);

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

describe("selectApps", () => {
  it("is newest first by default and leaves archived apps out", () => {
    expect(slugs(forQuery(""))).toEqual(["scratch", "docs-site", "billing-worker", "api-gateway"]);
  });

  it("Failing keeps failed provisioning and failed deploys", () => {
    expect(slugs(forQuery("view=failing"))).toEqual(["scratch", "billing-worker"]);
  });

  it("Archived keeps only archived apps", () => {
    expect(slugs(forQuery("view=archived"))).toEqual(["nightly-reports"]);
  });

  it("status matches the header's status, live before ready", () => {
    expect(appStatusKey(APPS[0])).toBe("live");
    expect(slugs(forQuery("status=live"))).toEqual(["billing-worker", "api-gateway"]);
  });

  it("matches project on slug or name, cluster on any environment", () => {
    expect(slugs(forQuery("project=Web%20properties"))).toEqual(["docs-site"]);
    expect(slugs(forQuery("cluster=stg-us-east-1"))).toEqual(["docs-site", "billing-worker"]);
  });

  it("kind and last deploy filter on topology and the health pulse", () => {
    expect(slugs(forQuery("kind=service-worker"))).toEqual(["billing-worker"]);
    expect(slugs(forQuery("deploy=never"))).toEqual(["scratch"]);
  });

  it("sorts by name when asked", () => {
    expect(slugs(forQuery("sort=name"))).toEqual([
      "api-gateway",
      "billing-worker",
      "docs-site",
      "scratch",
    ]);
  });

  it("lifts pins to the top of the page in hand", () => {
    expect(slugs(forQuery("", new Set(["api-gateway"])))[0]).toBe("api-gateway");
  });

  it("slices numbered pages and counts every match", () => {
    const out = selectApps(MANY, {
      filters: {},
      sort: APPS_LIST.defaultSort,
      page: 3,
      pageSize: 25,
    });
    expect(out.totalCount).toBe(60);
    expect(out.rows).toHaveLength(10);
  });
});
