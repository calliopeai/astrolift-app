import { describe, expect, it } from "vitest";

import {
  activeTabSection,
  type DetailTabSpec,
  detailRedirectTarget,
  detailTabHref,
  detailTabs,
  resolveDetailTab,
  sectionsBy,
  tabSectionHref,
} from "./detail-tabs-model";

type Key = "overview" | "logs" | "settings";

const TABS: DetailTabSpec<Key>[] = [
  { key: "overview", label: "Overview", segment: "", owns: ["topology"] },
  { key: "logs", label: "Logs", segment: "logs", owns: ["observability"] },
  { key: "settings", label: "Settings", segment: "settings", owns: ["config"] },
];
const SECTIONS = { logs: sectionsBy(["logs", "Logs"], ["metrics", "Metrics"]) };

describe("detail tabs model", () => {
  it("builds each tab's route, the first at the entity's root", () => {
    expect(detailTabHref(TABS, "/x", "acme", "overview")).toBe("/x/acme");
    expect(detailTabHref(TABS, "/x", "acme", "logs")).toBe("/x/acme/logs");
  });

  it("resolves the owner of a nested or absorbed path, falling back to the first tab", () => {
    expect(resolveDetailTab(TABS, "/x", "/x/acme/", "acme")).toBe("overview");
    expect(resolveDetailTab(TABS, "/x", "/x/acme/logs/web", "acme")).toBe("logs");
    expect(resolveDetailTab(TABS, "/x", "/x/acme/observability", "acme")).toBe("logs");
    expect(resolveDetailTab(TABS, "/x", "/elsewhere", "acme", "config")).toBe("settings");
    expect(resolveDetailTab(TABS, "/x", "/elsewhere", "acme")).toBe("overview");
  });

  it("resolves labels through the caller and marks one tab active", () => {
    const tabs = detailTabs(TABS, "/x", "acme", "/x/acme/settings", (l) => l.toUpperCase());
    expect(tabs.map((t) => [t.label, t.active])).toEqual([
      ["OVERVIEW", false],
      ["LOGS", false],
      ["SETTINGS", true],
    ]);
  });

  it("selects sections by query, the first being the bare route", () => {
    expect(activeTabSection(SECTIONS.logs, {})).toBe("logs");
    expect(activeTabSection(SECTIONS.logs, { section: "metrics" })).toBe("metrics");
    expect(activeTabSection(undefined, { section: "metrics" })).toBe("");
    expect(tabSectionHref(TABS, "/x", "acme", "logs", SECTIONS.logs[0])).toBe("/x/acme/logs");
    expect(tabSectionHref(TABS, "/x", "acme", "logs", SECTIONS.logs[1])).toBe(
      "/x/acme/logs?section=metrics"
    );
  });

  it("redirects a former route with its query kept and the section's set", () => {
    expect(
      detailRedirectTarget(TABS, SECTIONS, "/x", "acme", "logs", "metrics", {
        pod: "web-0",
        tag: ["a", "b"],
      })
    ).toBe("/x/acme/logs?pod=web-0&tag=a&tag=b&section=metrics");
    expect(detailRedirectTarget(TABS, SECTIONS, "/x", "acme", "settings", null, {})).toBe(
      "/x/acme/settings"
    );
  });
});
