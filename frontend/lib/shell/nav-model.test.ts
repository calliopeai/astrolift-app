import { describe, expect, it } from "vitest";

import { activeFor, areaSwitcher, NAV, visibleNav } from "./nav-model";

const only =
  (...keys: string[]) =>
  (m: string) =>
    keys.includes(m);

describe("visibleNav", () => {
  it("shows Home always and each area only when one of its functions is allowed", () => {
    expect(visibleNav(NAV, only()).map((a) => a.key)).toEqual(["home"]);
    expect(visibleNav(NAV, only("apps")).map((a) => a.key)).toEqual(["home", "apps"]);
  });

  it("keeps Workflows and Functions under the workflows module, inside Agents", () => {
    const agents = visibleNav(NAV, only("workflows")).find((a) => a.key === "agents");
    expect(agents?.groups.flatMap((g) => g.functions.map((f) => f.key))).toEqual([
      "workflows",
      "functions",
    ]);
  });

  it("lists the Agents functions of spec 44 §4.1, Workloads last", () => {
    const agents = NAV.find((a) => a.key === "agents");
    expect(agents?.groups.flatMap((g) => g.functions.map((f) => f.label))).toEqual([
      "Agents",
      "Fleet",
      "Approvals",
      "Workflows",
      "Runs",
      "Functions",
      "Skills",
      "Tools",
      "Environment specs",
      "Models",
      "Workloads",
    ]);
  });

  it("drops Admin's empty groups with the module", () => {
    expect(visibleNav(NAV, only("apps", "agents")).some((a) => a.key === "admin")).toBe(false);
  });
});

describe("activeFor", () => {
  it("picks the longest matching href", () => {
    expect(activeFor(NAV, "/agents/skills/42")).toEqual({ area: "agents", fn: "skills" });
    expect(activeFor(NAV, "/agents/support-bot")).toEqual({ area: "agents", fn: "agents" });
    expect(activeFor(NAV, "/clusters/conflict-astrolift/settings")).toEqual({
      area: "admin",
      fn: "clusters",
    });
  });

  it("does not match a prefix that is not a path segment", () => {
    expect(activeFor(NAV, "/appsx")).toBeNull();
  });
});

describe("areaSwitcher", () => {
  it("is the area's name over its functions, the active one checked", () => {
    const crumb = areaSwitcher(NAV, "agents", "workloads");
    expect(crumb.label).toBe("Agents");
    expect(crumb.switcher.filter((o) => o.active).map((o) => o.href)).toEqual(["/workloads"]);
  });
});
