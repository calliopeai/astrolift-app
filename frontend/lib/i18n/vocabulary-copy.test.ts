import { describe, expect, it } from "vitest";

import en from "@/messages/en.json";
import { WORKLOAD_KIND_META } from "@/lib/primitive";
import { NAV } from "@/lib/shell/nav-model";
import { TOPOLOGY_META } from "@/lib/topology";
import { APP_VIEWS, FLEET_VIEWS, MOTION_MODES, WORKFLOW_VIEWS } from "@/lib/viz-prefs";

/**
 * The shared vocabularies in lib/ name things by key; the screens show the
 * catalog's copy for each key (#2381). A key added on one side and not the
 * other would show the English declaration, or a raw key, so the two sets
 * must match. Key parity carries en.json's copy to every locale.
 */
const keys = (o: object) => Object.keys(o).sort();

describe("lib vocabularies have their copy", () => {
  it("nav model: every area, group and function", () => {
    const groups = NAV.flatMap((a) => a.groups).filter((g) => g.label);
    expect(groups.every((g) => g.key)).toBe(true);
    expect(keys(en.navModel.areas)).toEqual(NAV.map((a) => a.key).sort());
    expect(keys(en.navModel.groups)).toEqual(groups.map((g) => g.key!).sort());
    expect(keys(en.navModel.functions)).toEqual(
      NAV.flatMap((a) => a.groups.flatMap((g) => g.functions.map((f) => f.key))).sort()
    );
  });

  it("topology kinds: a name and a one-line blurb each", () => {
    expect(keys(en.vizRender.topology)).toEqual(keys(TOPOLOGY_META));
    expect(keys(en.vizRender.topologyBlurbs)).toEqual(keys(TOPOLOGY_META));
  });

  it("workload kinds", () => {
    expect(keys(en.appHomes.bundle.kinds)).toEqual(keys(WORKLOAD_KIND_META));
  });

  it("visualization styles, in settings and in the views' own pickers", () => {
    for (const [group, styles] of [
      ["fleet", FLEET_VIEWS],
      ["workflow", WORKFLOW_VIEWS],
      ["app", APP_VIEWS],
    ] as const) {
      expect(keys(en.visualizationSettings[group])).toEqual(keys(styles));
      expect(keys(en.vizRender.styles[group])).toEqual(keys(styles));
    }
    expect(keys(en.visualizationSettings.motion)).toEqual(keys(MOTION_MODES));
  });
});
