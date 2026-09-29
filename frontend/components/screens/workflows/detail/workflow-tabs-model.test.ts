import { describe, expect, it } from "vitest";

import {
  activeWorkflowSection,
  WORKFLOW_FORMER_ROUTES,
  WORKFLOW_TAB_SECTIONS,
  WORKFLOW_TABS,
  workflowRedirectTarget,
  workflowSectionHref,
  workflowTabHref,
  workflowTabs,
  resolveWorkflowTab,
} from "./workflow-tabs-model";

describe("workflow tabs", () => {
  it("is one row of four tabs, named by function, in spec 44 §5.2's order", () => {
    expect(workflowTabs("nightly-sync", "/workflows/nightly-sync").map((t) => t.label)).toEqual([
      "Builder",
      "Runs",
      "Triggers",
      "Settings",
    ]);
  });

  it("puts Builder at the workflow's root and every other tab on its own route", () => {
    expect(workflowTabHref("nightly-sync", "builder")).toBe("/workflows/nightly-sync");
    expect(workflowTabHref("nightly-sync", "runs")).toBe("/workflows/nightly-sync/runs");
    expect(workflowTabHref("nightly-sync", "triggers")).toBe("/workflows/nightly-sync/triggers");
    expect(workflowTabHref("nightly-sync", "settings")).toBe("/workflows/nightly-sync/settings");
  });

  it("lights the tab that absorbed a former pillar route", () => {
    expect(resolveWorkflowTab("/workflows/nightly-sync", "nightly-sync")).toBe("builder");
    expect(resolveWorkflowTab("/workflows/nightly-sync/build", "nightly-sync")).toBe("builder");
    expect(resolveWorkflowTab("/workflows/nightly-sync/builder", "nightly-sync")).toBe("builder");
    expect(resolveWorkflowTab("/workflows/nightly-sync/run", "nightly-sync")).toBe("runs");
    expect(resolveWorkflowTab("/workflows/nightly-sync/observe", "nightly-sync")).toBe("runs");
    expect(resolveWorkflowTab("/workflows/nightly-sync/triggers", "nightly-sync")).toBe("triggers");
    expect(resolveWorkflowTab("/workflows/nightly-sync/unknown", "nightly-sync")).toBe("builder");
  });

  it("marks exactly one tab active", () => {
    const tabs = workflowTabs("nightly-sync", "/workflows/nightly-sync/settings");
    expect(tabs.filter((t) => t.active).map((t) => t.key)).toEqual(["settings"]);
  });

  it("encodes the slug as usePathname does", () => {
    expect(workflowTabHref("a b", "runs")).toBe("/workflows/a%20b/runs");
    expect(resolveWorkflowTab("/workflows/a%20b/runs", "a b")).toBe("runs");
  });

  it("stays within eight tabs", () => {
    expect(WORKFLOW_TABS.length).toBeLessThanOrEqual(8);
  });
});

describe("workflow tab sections", () => {
  it("keeps Settings on General then one Danger zone", () => {
    expect(WORKFLOW_TAB_SECTIONS.settings?.map((s) => s.id)).toEqual(["general", "danger-zone"]);
    expect(activeWorkflowSection("settings", {})).toBe("general");
    expect(activeWorkflowSection("settings", { section: "danger-zone" })).toBe("danger-zone");
    expect(activeWorkflowSection("settings", { section: "nope" })).toBe("general");
  });

  it("links the default section to the bare tab route", () => {
    expect(workflowSectionHref("nightly-sync", "settings", "general")).toBe(
      "/workflows/nightly-sync/settings"
    );
    expect(workflowSectionHref("nightly-sync", "settings", "danger-zone")).toBe(
      "/workflows/nightly-sync/settings?section=danger-zone"
    );
    expect(workflowSectionHref("nightly-sync", "runs", "anything")).toBe(
      "/workflows/nightly-sync/runs"
    );
  });
});

describe("workflow former routes", () => {
  it("maps every BROCS pillar route and the standalone builder", () => {
    expect(Object.keys(WORKFLOW_FORMER_ROUTES).sort()).toEqual([
      "build",
      "builder",
      "observe",
      "run",
    ]);
  });

  it("sends Build and Builder to the root, Run and Observe to Runs", () => {
    expect(workflowRedirectTarget("build", "nightly-sync", {})).toBe("/workflows/nightly-sync");
    expect(workflowRedirectTarget("builder", "nightly-sync", {})).toBe("/workflows/nightly-sync");
    expect(workflowRedirectTarget("run", "nightly-sync", {})).toBe("/workflows/nightly-sync/runs");
    expect(workflowRedirectTarget("observe", "nightly-sync", {})).toBe(
      "/workflows/nightly-sync/runs"
    );
  });

  it("keeps the query an old link carried, so ?run= still opens that run", () => {
    expect(workflowRedirectTarget("observe", "outbound", { run: "run-8" })).toBe(
      "/workflows/outbound/runs?run=run-8"
    );
    expect(workflowRedirectTarget("observe", "a b", { run: ["r1", "r2"] })).toBe(
      "/workflows/a%20b/runs?run=r1&run=r2"
    );
  });

  it("lands an unknown route on Builder", () => {
    expect(workflowRedirectTarget("nope", "nightly-sync", {})).toBe("/workflows/nightly-sync");
  });
});
