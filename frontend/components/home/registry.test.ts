import { describe, expect, it } from "vitest";

import { BOTH, NO_ACCESS, ONLY_AGENTS, ONLY_APPS, OPERATOR } from "./fixtures";
import {
  defaultLayoutFor,
  HOME_LAYOUT_KEYS,
  HOME_LAYOUTS,
  HOME_PANELS,
  type HomeAccess,
  isHomeLayoutKey,
  meets,
  offeredLayouts,
  resolveLayout,
  visiblePanels,
} from "./registry";

const keys = (a: HomeAccess) => offeredLayouts(a).map((l) => l.key);
const titles = (k: keyof typeof HOME_LAYOUTS, a: HomeAccess = OPERATOR) =>
  visiblePanels(HOME_LAYOUTS[k], a).map((p) => p.title);

describe("home registry", () => {
  it("ships the four layouts with the spec's panels in order", () => {
    expect(HOME_LAYOUT_KEYS).toEqual(["apps", "agents", "builder", "operator"]);
    expect(titles("apps")).toEqual([
      "Waiting on you",
      "Failing",
      "My apps",
      "Recent deployments",
      "Traffic & errors",
    ]);
    expect(titles("agents")).toEqual([
      "Waiting on you",
      "Failed runs",
      "Running now",
      "My agents",
      "Runs & spend",
    ]);
    expect(titles("builder")).toEqual([
      "Waiting on you",
      "Failing",
      "Activity",
      "KPIs",
      "Deployments",
      "Agent runs",
    ]);
    expect(titles("operator")).toEqual([
      "Alerts",
      "Clusters",
      "Waiting on you",
      "Platform activity",
      "Spend & quota",
    ]);
  });

  it("puts Waiting on you in every layout, in the first three, visible to anyone", () => {
    for (const k of HOME_LAYOUT_KEYS) {
      const i = HOME_LAYOUTS[k].panels.indexOf("waiting");
      expect(i, k).toBeGreaterThanOrEqual(0);
      expect(i, k).toBeLessThan(3);
    }
    expect(meets(HOME_PANELS.waiting.requires, NO_ACCESS)).toBe(true);
  });

  it("names every panel once, keyed by its own key", () => {
    for (const [k, p] of Object.entries(HOME_PANELS)) expect(p.key).toBe(k);
    for (const k of HOME_LAYOUT_KEYS) {
      const panels = HOME_LAYOUTS[k].panels;
      expect(new Set(panels).size, k).toBe(panels.length);
    }
  });

  it("offers only the layouts whose modules the person has", () => {
    expect(keys(ONLY_APPS)).toEqual(["apps"]);
    expect(keys(ONLY_AGENTS)).toEqual(["agents"]);
    expect(keys(BOTH)).toEqual(["apps", "agents", "builder"]);
    expect(keys(OPERATOR)).toEqual(["apps", "agents", "builder", "operator"]);
    expect(keys(NO_ACCESS)).toEqual([]);
  });

  it("does not draw a panel the person cannot see", () => {
    const noAudit: HomeAccess = {
      modules: BOTH.modules,
      permissions: new Set(["app.read", "agent.read"]),
    };
    expect(visiblePanels(HOME_LAYOUTS.builder, noAudit).map((p) => p.key)).toEqual([
      "waiting",
      "failing",
      "kpis",
      "deployments",
      "agent-runs",
    ]);
    // Builder drawn for someone who lost Agents keeps only the Apps side.
    expect(visiblePanels(HOME_LAYOUTS.builder, ONLY_APPS).map((p) => p.key)).toEqual([
      "waiting",
      "failing",
      "activity",
      "kpis",
      "deployments",
    ]);
  });

  it("defaults from access: only Apps, only Agents, both, admin", () => {
    expect(defaultLayoutFor(ONLY_APPS)).toBe("apps");
    expect(defaultLayoutFor(ONLY_AGENTS)).toBe("agents");
    expect(defaultLayoutFor(BOTH)).toBe("builder");
    expect(defaultLayoutFor(OPERATOR)).toBe("operator");
    expect(defaultLayoutFor({ ...NO_ACCESS, modules: new Set(["admin"]) })).toBe("operator");
    expect(defaultLayoutFor(NO_ACCESS)).toBeNull();
  });

  it("always defaults to a layout it offers", () => {
    for (const a of [ONLY_APPS, ONLY_AGENTS, BOTH, OPERATOR]) {
      expect(keys(a)).toContain(defaultLayoutFor(a));
    }
  });

  it("draws the saved layout while it is offered, else the default", () => {
    expect(resolveLayout("apps", BOTH)?.key).toBe("apps");
    expect(resolveLayout(null, BOTH)?.key).toBe("builder");
    expect(resolveLayout("builder", ONLY_AGENTS)?.key).toBe("agents");
    expect(resolveLayout("operator", BOTH)?.key).toBe("builder");
    expect(resolveLayout(null, NO_ACCESS)).toBeNull();
  });

  it("validates layout keys against the registry", () => {
    expect(isHomeLayoutKey("builder")).toBe(true);
    expect(isHomeLayoutKey("finance")).toBe(false);
    expect(isHomeLayoutKey(3)).toBe(false);
  });

  it("keeps every layout within two rows of panels plus a strip (rule 1)", () => {
    for (const k of HOME_LAYOUT_KEYS) {
      const columns = HOME_LAYOUTS[k].panels.reduce((n, p) => n + HOME_PANELS[p].span, 0);
      expect(columns, k).toBeLessThanOrEqual(36);
    }
  });
});
